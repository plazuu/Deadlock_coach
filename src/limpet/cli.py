"""``limpet`` command-line interface.

Run `limpet help` for the full command list with descriptions (generated
from each command's own docstring, so it can't go stale the way a
hand-written list here would) or `limpet <command> --help` for one command's
options.
"""

from __future__ import annotations

import datetime as _dt
import json
import signal
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import anthropic
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import paths
from .api.client import DeadlockAPIError, DeadlockClient, MetadataNotReady
from .api.models import MatchHistoryEntry, PlayerRank
from .assets import Assets
from .coach.analyze import CoachError, collect_batch_reports, poll_batch, submit_batch
from .coach.analyze import analyze_match as run_coach
from .coach.briefing import build as build_briefing
from .coach.briefing import estimate_tokens
from .coach.builds import build_item_context
from .coach.focus import reconcile as reconcile_focus_areas
from .coach.schema import CoachingReport
from .config import Settings, load_settings, write_config
from .features.benchmarks import attach as attach_benchmarks
from .features.benchmarks import fetch_hero_distributions
from .features.extract import extract as extract_features
from .ids import InvalidSteamID, to_account_id, to_steamid64
from .ingest import (
    cached_metadata,
    fetch_metadata,
    ingest_match,
    sync_match_history,
    upsert_from_metadata,
)
from .notify import notify
from .parse.metadata import MatchView, PlayerNotInMatch
from .parse.metadata import load as load_match
from .report.markdown import render as render_report
from .report.progress import recompute_snapshot
from .report.progress import regenerate as regenerate_progress
from .store import db

app = typer.Typer(add_completion=False, help="A local AI coach for Deadlock.")
assets_app = typer.Typer(help="Manage the local static-asset cache.")
app.add_typer(assets_app, name="assets")
console = Console()


def _client(settings: Settings) -> DeadlockClient:
    return DeadlockClient(api_key=settings.deadlock_api_key)


def _require_account(settings: Settings) -> int:
    try:
        return settings.account_id
    except (ValueError, InvalidSteamID) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e


@app.command("help")
def help_cmd() -> None:
    """List every command and what it does."""
    click_app = typer.main.get_command(app)
    table = Table(title="limpet commands", show_header=True, header_style="bold")
    table.add_column("command")
    table.add_column("description")
    for name, cmd in click_app.commands.items():
        subcommands = getattr(cmd, "commands", None)
        if subcommands:
            for sub_name, sub_cmd in subcommands.items():
                table.add_row(f"{name} {sub_name}", sub_cmd.get_short_help_str(limit=100))
        else:
            table.add_row(name, cmd.get_short_help_str(limit=100))
    console.print(table)
    console.print("\n[dim]Run `limpet <command> --help` for a command's full options.[/dim]")


@app.command()
def init(
    steam_id: Annotated[str, typer.Option(prompt="Your Steam ID / SteamID64 / profile URL")],
    deadlock_api_key: Annotated[
        str, typer.Option(prompt="deadlock-api.com API key (blank to skip)")
    ] = "",
    anthropic_api_key: Annotated[
        str,
        typer.Option(prompt="Anthropic API key (blank = use ANTHROPIC_API_KEY / ant auth)"),
    ] = "",
) -> None:
    """Write configuration, verify API access, and cache static assets."""
    try:
        account_id = to_account_id(steam_id)
    except InvalidSteamID as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    paths.ensure_tree()
    write_config(
        {
            "steam_id": steam_id,
            "deadlock_api_key": deadlock_api_key,
            "anthropic_api_key": anthropic_api_key,
        }
    )
    console.print(
        f"Resolved account id: [bold]{account_id}[/bold] (SteamID64 {to_steamid64(account_id)})"
    )
    console.print(f"Config written to [dim]{paths.config_path()}[/dim]")

    settings = load_settings()
    with _client(settings) as client:
        console.print("Caching static assets (heroes, items, ranks)…")
        Assets(client).refresh(force=True)
        try:
            rank = PlayerRank.model_validate(client.player_rank(account_id))
            assets = Assets(client)
            console.print(
                f"Current rank: [bold]{assets.rank_name(rank.average_badge)}[/bold]"
                if rank.is_ranked
                else "Current rank: [dim]unranked / in placements[/dim]"
            )
        except DeadlockAPIError as e:
            console.print(f"[yellow]Could not read rank: {e}[/yellow]")

    db.connect()  # create the database file
    console.print("[green]Ready.[/green] Try [bold]limpet sync[/bold].")


@app.command()
def whoami() -> None:
    """Show the resolved account id, current rank, and most-played heroes."""
    settings = load_settings()
    account_id = _require_account(settings)
    console.print(f"account_id : [bold]{account_id}[/bold]")
    console.print(f"SteamID64  : {to_steamid64(account_id)}")
    console.print(f"data dir   : {paths.data_dir()}")
    with _client(settings) as client:
        assets = Assets(client)
        try:
            rank = PlayerRank.model_validate(client.player_rank(account_id))
            console.print(
                f"rank       : {assets.rank_name(rank.average_badge)} (badge {rank.average_badge})"
            )
        except DeadlockAPIError as e:
            console.print(f"rank       : [yellow]{e}[/yellow]")

        with db.session() as conn:
            top = db.top_heroes(conn, account_id)
        if top:
            heroes = ", ".join(
                f"{assets.hero_name(row['hero_id'])} ({row['games']})" for row in top
            )
            console.print(f"top heroes : {heroes}")


@assets_app.command("refresh")
def assets_refresh() -> None:
    """Force-refresh the hero/item/rank cache."""
    settings = load_settings()
    with _client(settings) as client:
        Assets(client).refresh(force=True)
    console.print(f"[green]Assets refreshed[/green] -> {paths.assets_cache_dir()}")


@app.command()
def sync(
    force_refetch: Annotated[
        bool, typer.Option(help="Ask the API to refetch history from Steam (rate limited).")
    ] = False,
) -> None:
    """Pull match history into the local database."""
    settings = load_settings()
    account_id = _require_account(settings)
    with _client(settings) as client, db.session() as conn:
        new = sync_match_history(client, conn, account_id, force_refetch=force_refetch)
        if not new:
            console.print("Up to date — no new matches.")
            if not force_refetch:
                console.print(
                    "[dim]This reads the API's cached match history, which doesn't always "
                    "include a match you just finished. Try `sync --force-refetch` (rate "
                    "limited, ~1-10/hour) to ask it to refresh from Steam.[/dim]"
                )
            return
        console.print(f"[green]{len(new)} new match(es):[/green]")
        _print_matches(new[:20], Assets(client))
    if len(new) > 20:
        console.print(f"[dim]…and {len(new) - 20} more.[/dim]")


@app.command()
def matches(
    limit: Annotated[int, typer.Option(help="How many recent matches to show.")] = 15,
    force_refetch: Annotated[
        bool,
        typer.Option(
            help="Ask the API to refetch history from Steam (rate limited, ~1-10/hour) — "
            "use this if a match you just finished isn't showing up."
        ),
    ] = False,
) -> None:
    """List recent match history (from the API, not the local db)."""
    settings = load_settings()
    account_id = _require_account(settings)
    with _client(settings) as client:
        raw = client.match_history(account_id, force_refetch=force_refetch)
        entries = [MatchHistoryEntry.model_validate(r) for r in raw]
        entries.sort(key=lambda e: e.start_time, reverse=True)
        _print_matches(entries[:limit], Assets(client))


@app.command()
def fetch(
    match_id: Annotated[int, typer.Argument(help="Match id to fetch metadata for.")],
) -> None:
    """Fetch and cache full metadata for a single match."""
    settings = load_settings()
    with _client(settings) as client, db.session() as conn:
        try:
            path = ingest_match(client, conn, match_id)
        except MetadataNotReady as e:
            console.print(f"[yellow]{e}[/yellow] Try again in a few minutes.")
            raise typer.Exit(2) from e
        except DeadlockAPIError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e
    console.print(f"[green]Cached[/green] -> {path}")


@dataclass
class _PreparedMatch:
    """A match with features/benchmarks saved and a briefing ready to send —
    everything `analyze` and `backfill` need before the LLM call, which they
    then do differently (one live call vs. a Batch API submission)."""

    match_id: int
    view: MatchView
    feats_dict: dict[str, Any]
    hero_name: str
    won: bool
    briefing: dict[str, Any]
    kills: int | None
    deaths: int | None
    assists: int | None


def _prepare_match(
    client: DeadlockClient,
    conn: sqlite3.Connection,
    account_id: int,
    assets: Assets,
    settings: Settings,
    match_id: int,
    active_focus_areas: list[dict[str, Any]],
) -> _PreparedMatch:
    """Ingest, extract features, attach benchmarks, and build a briefing for
    one match. Raises `MetadataNotReady` / `DeadlockAPIError` /
    `PlayerNotInMatch` on failure — callers decide how each should be
    handled (stop entirely, skip this match, retry later)."""
    meta = cached_metadata(match_id)
    if meta is None:
        meta, _path = fetch_metadata(client, match_id)

    # Upserts a base `matches` row from the metadata itself, so this works
    # standalone without a prior `sync` (e.g. on a friend's match).
    upsert_from_metadata(conn, meta, account_id)

    now = int(time.time())
    raw_path = paths.match_cache_dir(match_id) / "metadata.json"
    conn.execute(
        "UPDATE matches SET raw_meta_path = ?, ingested_at = ? WHERE match_id = ?",
        (str(raw_path), now, match_id),
    )

    view = load_match(meta)
    features = extract_features(view, account_id, assets)
    feats_dict = features.to_dict()

    distributions = None
    try:
        distributions = fetch_hero_distributions(
            client, view.player(account_id).get("hero_id", 0), view.average_badge(account_id)
        )
        attach_benchmarks(feats_dict, distributions)
    except DeadlockAPIError as e:
        console.print(
            f"[yellow]Benchmarks unavailable for {match_id}, showing raw features: {e}[/yellow]"
        )

    db.save_features(conn, match_id, feats_dict, distributions, now)
    conn.execute("UPDATE matches SET analyzed_at = ? WHERE match_id = ?", (now, match_id))

    player = view.player(account_id)
    hero_name = assets.hero_name(player.get("hero_id", 0))
    won = bool(view.winning_team is not None and player.get("team") == view.winning_team)
    item_builds = build_item_context(view, account_id, assets)
    briefing = build_briefing(
        feats_dict,
        match_id=match_id,
        hero_name=hero_name,
        won=won,
        duration_s=view.duration_s,
        rank_name=assets.rank_name(view.average_badge(account_id)),
        active_focus_areas=active_focus_areas,
        item_builds=item_builds,
    )
    est_tokens = estimate_tokens(json.dumps(briefing))
    if est_tokens > settings.briefing_token_budget:
        console.print(
            f"[yellow]Briefing for {match_id} is ~{est_tokens} tokens, over the "
            f"{settings.briefing_token_budget} budget — sending anyway.[/yellow]"
        )

    return _PreparedMatch(
        match_id=match_id,
        view=view,
        feats_dict=feats_dict,
        hero_name=hero_name,
        won=won,
        briefing=briefing,
        kills=player.get("kills"),
        deaths=player.get("deaths"),
        assists=player.get("assists"),
    )


@dataclass
class _LiveResult:
    """What came out of a live (non-batch) coaching call for one match.
    `report` is None if the LLM call itself failed — features/benchmarks were
    still saved by `_prepare_match` either way, matching `analyze()`'s
    long-standing "keep what could be computed" behavior."""

    report: CoachingReport | None
    markdown: str | None
    report_path: Path | None
    usage: Any | None


def _run_live_pipeline(
    anthropic_client: anthropic.Anthropic,
    conn: sqlite3.Connection,
    settings: Settings,
    prepared: _PreparedMatch,
    now: int,
) -> _LiveResult:
    """LLM call -> render -> save -> reconcile focus areas, for one match.

    Shared by `analyze` (one match, synchronous) and `watch` (one match at a
    time as they arrive) — `backfill` uses the Batch API path instead
    (`coach/analyze.py::submit_batch`/`poll_batch`/`collect_batch_reports`).
    """
    try:
        report, usage = run_coach(anthropic_client, prepared.briefing, model=settings.model)
    except CoachError as e:
        console.print(f"[yellow]{prepared.match_id}: coaching report failed: {e}[/yellow]")
        return _LiveResult(report=None, markdown=None, report_path=None, usage=None)

    view = prepared.view
    markdown = render_report(
        report,
        match_id=prepared.match_id,
        hero_name=prepared.hero_name,
        won=prepared.won,
        played_at=view.raw.get("start_time", 0),
        duration_s=view.duration_s,
    )
    db.save_report(conn, prepared.match_id, settings.model, markdown, report.model_dump(), now)
    paths.reports_dir().mkdir(parents=True, exist_ok=True)
    report_path = paths.reports_dir() / f"{view.raw.get('start_time', 0)}_{prepared.match_id}.md"
    report_path.write_text(markdown)

    try:
        reconcile_focus_areas(anthropic_client, conn, prepared.match_id, report, now)
    except CoachError as e:
        console.print(f"[yellow]{prepared.match_id}: focus-area tracking skipped: {e}[/yellow]")

    return _LiveResult(report=report, markdown=markdown, report_path=report_path, usage=usage)


@app.command()
def analyze(
    match_id: Annotated[
        int | None,
        typer.Argument(help="Match id to analyze. Omit to analyze your most recent match."),
    ] = None,
    report: Annotated[
        bool,
        typer.Option(help="Also call Claude for a coaching report (needs an Anthropic key)."),
    ] = True,
    force_refetch: Annotated[
        bool,
        typer.Option(
            help="When match_id is omitted, ask the API to refetch history from Steam "
            "(rate limited, ~1-10/hour) — use this if your most recent match doesn't "
            "get picked."
        ),
    ] = False,
) -> None:
    """Compute micro/macro features + benchmarks, then (by default) a coaching report."""
    settings = load_settings()
    account_id = _require_account(settings)
    with _client(settings) as client, db.session() as conn:
        if match_id is None:
            history = [
                MatchHistoryEntry.model_validate(r)
                for r in client.match_history(account_id, force_refetch=force_refetch)
            ]
            if not history:
                console.print("[red]No match history found for this account.[/red]")
                raise typer.Exit(1)
            match_id = max(history, key=lambda e: e.start_time).match_id

        assets = Assets(client)
        focus_areas = [
            {"dimension": r["dimension"], "theme": r["theme"], "status": r["status"]}
            for r in db.active_focus_areas(conn)
        ]
        try:
            prepared = _prepare_match(
                client, conn, account_id, assets, settings, match_id, focus_areas
            )
        except MetadataNotReady as e:
            console.print(f"[yellow]{e}[/yellow] Try again in a few minutes.")
            raise typer.Exit(2) from e
        except DeadlockAPIError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e
        except PlayerNotInMatch as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e

        if not report:
            console.print_json(json.dumps(prepared.feats_dict))
            return

        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
        now = int(time.time())
        result = _run_live_pipeline(anthropic_client, conn, settings, prepared, now)
        if result.report is None:
            console.print("[dim]Features + benchmarks were still saved.[/dim]")
            console.print_json(json.dumps(prepared.feats_dict))
            return
        regenerate_progress(conn)

    console.print(result.markdown)
    console.print(
        f"\n[dim]Saved -> {result.report_path}  "
        f"(in {result.usage.input_tokens} + out {result.usage.output_tokens} tokens) "
        f"-- progress -> {paths.progress_path()}[/dim]"
    )


@app.command()
def backfill(
    last: Annotated[int, typer.Option("--last", help="How many recent matches to backfill.")],
    reanalyze: Annotated[
        bool,
        typer.Option(help="Reprocess matches that already have a stored report."),
    ] = False,
    force_refetch: Annotated[
        bool,
        typer.Option(help="Refresh match history from Steam first (rate limited)."),
    ] = False,
) -> None:
    """Ingest + analyze recent match history via the Anthropic Batch API (50% cost).

    Every report generated in one backfill run sees the same tracked-focus-areas
    snapshot from before the run started, not each other's results — the Batch
    API call is what makes this cheap, and that call has to be prepared before
    any of its own results exist. Also backfills the daily Trend-table row for
    every day touched, which a plain `analyze` only ever does for today.
    """
    settings = load_settings()
    account_id = _require_account(settings)

    with _client(settings) as client, db.session() as conn:
        history = [
            MatchHistoryEntry.model_validate(r)
            for r in client.match_history(account_id, force_refetch=force_refetch)
        ]
        history.sort(key=lambda e: e.start_time, reverse=True)
        candidates = [e.match_id for e in history[:last]]
        if not reanalyze:
            already = db.analyzed_match_ids(conn, candidates)
            candidates = [m for m in candidates if m not in already]
        if not candidates:
            console.print("[dim]Nothing to backfill — every candidate is already analyzed.[/dim]")
            return

        by_start = {e.match_id: e.start_time for e in history}
        candidates.sort(key=lambda m: by_start.get(m, 0))  # oldest first

        console.print(
            f"Preparing {len(candidates)} match(es) "
            "(features + benchmarks + briefing, no LLM call yet)…"
        )
        assets = Assets(client)
        focus_areas = [
            {"dimension": r["dimension"], "theme": r["theme"], "status": r["status"]}
            for r in db.active_focus_areas(conn)
        ]

        prepared: list[_PreparedMatch] = []
        rate_limited = False
        for match_id in candidates:
            try:
                prepared.append(
                    _prepare_match(
                        client, conn, account_id, assets, settings, match_id, focus_areas
                    )
                )
            except MetadataNotReady as e:
                console.print(f"[yellow]{match_id}: {e} — skipping for now.[/yellow]")
            except PlayerNotInMatch as e:
                console.print(f"[yellow]{match_id}: {e} — skipping.[/yellow]")
            except DeadlockAPIError as e:
                remaining = len(candidates) - candidates.index(match_id) - 1
                console.print(
                    f"[red]{match_id}: {e}[/red] — likely the metadata rate limit "
                    f"(3/hour without a deadlock-api key). Stopping here; {remaining} "
                    "match(es) left for a later `backfill` run."
                )
                rate_limited = True
                break

        if not prepared:
            console.print("[red]No matches could be prepared — nothing to backfill.[/red]")
            return

    # db session + deadlock-api client closed here — the batch can take up to
    # 24h, no need to hold either open across the poll.

    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    briefings = {p.match_id: p.briefing for p in prepared}
    console.print(f"Submitting {len(prepared)} match(es) to the Anthropic Batch API…")
    try:
        batch_id = submit_batch(anthropic_client, briefings, model=settings.model)
    except CoachError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[dim]Features + benchmarks were still saved for the prepared matches.[/dim]")
        return

    def _on_tick(batch: Any) -> None:
        rc = batch.request_counts
        console.print(
            f"[dim]batch {batch_id}: {batch.processing_status} "
            f"(processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored})[/dim]"
        )

    poll_batch(anthropic_client, batch_id, on_tick=_on_tick)
    results = collect_batch_reports(anthropic_client, batch_id)

    succeeded = 0
    failed = 0
    touched_days: set[str] = set()
    now = int(time.time())
    with db.session() as conn:
        for p in prepared:  # already chronological — matters for focus-area continuity
            result = results.get(p.match_id)
            if result is None:
                console.print(
                    f"[yellow]{p.match_id}: no batch result returned — skipping.[/yellow]"
                )
                failed += 1
                continue
            if isinstance(result, CoachError):
                console.print(f"[yellow]{p.match_id}: {result} — skipping.[/yellow]")
                failed += 1
                continue

            markdown = render_report(
                result,
                match_id=p.match_id,
                hero_name=p.hero_name,
                won=p.won,
                played_at=p.view.raw.get("start_time", 0),
                duration_s=p.view.duration_s,
            )
            db.save_report(conn, p.match_id, settings.model, markdown, result.model_dump(), now)
            paths.reports_dir().mkdir(parents=True, exist_ok=True)
            report_path = paths.reports_dir() / f"{p.view.raw.get('start_time', 0)}_{p.match_id}.md"
            report_path.write_text(markdown)

            try:
                reconcile_focus_areas(anthropic_client, conn, p.match_id, result, now)
            except CoachError as e:
                console.print(f"[yellow]{p.match_id}: focus-area tracking skipped: {e}[/yellow]")

            played_day = _dt.datetime.fromtimestamp(p.view.raw.get("start_time", 0)).strftime(
                "%Y-%m-%d"
            )
            touched_days.add(played_day)
            succeeded += 1

        for day in sorted(touched_days):
            recompute_snapshot(conn, day=day)
        regenerate_progress(conn)

    console.print(
        f"\n[green]Backfill done.[/green] {succeeded} report(s) saved, {failed} failed/skipped, "
        f"{len(touched_days)} day(s) backfilled into the Trend table -- "
        f"progress -> {paths.progress_path()}"
    )
    if rate_limited:
        console.print(
            "[yellow]Stopped early on the metadata rate limit — re-run `backfill` later "
            "to pick up the rest.[/yellow]"
        )


@app.command()
def report(
    match_id: Annotated[int, typer.Argument(help="Match id to re-render.")],
) -> None:
    """Re-render a match's coaching report from stored data (no re-fetch, no LLM)."""
    settings = load_settings()
    with db.session() as conn:
        report_row = conn.execute(
            "SELECT * FROM reports WHERE match_id = ? ORDER BY id DESC LIMIT 1", (match_id,)
        ).fetchone()
        match_row = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()

    if report_row is None:
        console.print(
            f"[red]No stored report for match {match_id}.[/red] "
            f"Run `limpet analyze {match_id}` first."
        )
        raise typer.Exit(1)
    if match_row is None:
        console.print(f"[red]No local record of match {match_id}.[/red]")
        raise typer.Exit(1)

    coaching_report = CoachingReport.model_validate(json.loads(report_row["structured_json"]))
    with _client(settings) as client:
        hero_name = Assets(client).hero_name(match_row["hero_id"])

    markdown = render_report(
        coaching_report,
        match_id=match_id,
        hero_name=hero_name,
        won=bool(match_row["won"]),
        played_at=match_row["played_at"],
        duration_s=match_row["duration_s"],
    )
    console.print(markdown)


@app.command()
def progress() -> None:
    """Show the running coaching profile (regenerated after every `analyze`)."""
    path = paths.progress_path()
    if not path.exists():
        console.print("[dim]No progress tracked yet — run `limpet analyze <match_id>` first.[/dim]")
        return
    console.print(Markdown(path.read_text()))


# -- watch -------------------------------------------------------------
# First-pass, tunable constants (same honesty convention as
# features/macro/farm_stealing.py's WINDOW_S/RATE_MULTIPLIER): MetadataNotReady
# is expected ("a match is queryable minutes, sometimes longer, after it
# ends" — docs/api-notes.md) so it gets generous retries; any other
# DeadlockAPIError/PlayerNotInMatch is unlikely to self-resolve, so far fewer.
MAX_METADATA_ATTEMPTS = 20
MAX_OTHER_ATTEMPTS = 3
BACKOFF_CAP_S = 2 * 3600


def _reschedule(
    conn: sqlite3.Connection, row: sqlite3.Row, error: Exception, *, max_attempts: int, base_s: int
) -> None:
    match_id = row["match_id"]
    attempts = row["attempts"] + 1
    now = int(time.time())
    if attempts >= max_attempts:
        console.print(f"[red]{match_id}: giving up after {attempts} attempts: {error}[/red]")
        db.fail_watch(conn, match_id, str(error), now)
        return
    delay = min(base_s * (2 ** (attempts - 1)), BACKOFF_CAP_S)
    console.print(f"[yellow]{match_id}: {error} — retrying in ~{delay // 60} min.[/yellow]")
    db.reschedule_watch(conn, match_id, now + delay, str(error), now)


def _append_digest(prepared: _PreparedMatch, result: _LiveResult) -> None:
    paths.digests_dir().mkdir(parents=True, exist_ok=True)
    day_path = paths.digests_dir() / f"{_dt.datetime.now().strftime('%Y-%m-%d')}.md"
    outcome = "Win" if prepared.won else "Loss"
    time_str = _dt.datetime.now().strftime("%H:%M")
    line = f"- {time_str} **{prepared.hero_name}** — {outcome} (match {prepared.match_id})"
    if result.report is None:
        line += " — _coaching report failed; features/benchmarks saved_"
    with day_path.open("a") as f:
        f.write(line + "\n")


def _run_watch_cycle(
    client: DeadlockClient,
    conn: sqlite3.Connection,
    account_id: int,
    assets: Assets,
    settings: Settings,
    anthropic_client: anthropic.Anthropic,
    *,
    stop_requested: Any,
) -> None:
    """One poll: sync history, enqueue new (mode-filtered) matches, drain due
    queue items. Pulled out of `watch`'s loop so it's unit-testable without an
    infinite loop or real sleeps."""
    new = sync_match_history(client, conn, account_id)
    now = int(time.time())
    for entry in new:
        if entry.match_mode_name in settings.match_modes:
            db.enqueue_watch(conn, entry.match_id, now)

    base_s = settings.poll_interval_minutes * 60
    for row in db.due_watch_items(conn, now):
        if stop_requested():
            break
        match_id = row["match_id"]
        focus_areas = [
            {"dimension": r["dimension"], "theme": r["theme"], "status": r["status"]}
            for r in db.active_focus_areas(conn)
        ]
        try:
            prepared = _prepare_match(
                client, conn, account_id, assets, settings, match_id, focus_areas
            )
        except MetadataNotReady as e:
            _reschedule(conn, row, e, max_attempts=MAX_METADATA_ATTEMPTS, base_s=base_s)
            continue
        except (DeadlockAPIError, PlayerNotInMatch) as e:
            _reschedule(conn, row, e, max_attempts=MAX_OTHER_ATTEMPTS, base_s=base_s)
            continue

        result = _run_live_pipeline(anthropic_client, conn, settings, prepared, int(time.time()))
        db.mark_watch_done(conn, match_id, int(time.time()))
        _append_digest(prepared, result)
        outcome = "Win" if prepared.won else "Loss"
        kda = f"{prepared.kills}/{prepared.deaths}/{prepared.assists}"
        notify("Limpet", f"{prepared.hero_name}({kda}) - {outcome}({match_id})")

    regenerate_progress(conn)


@app.command()
def watch(
    once: Annotated[
        bool, typer.Option(help="Run a single poll cycle and exit, instead of looping forever.")
    ] = False,
) -> None:
    """Long-running poller: watch for new matches and analyze them automatically.

    Restart-safe — queued matches live in SQLite (`watch_queue`), not in
    memory, so killing and restarting `watch` picks up where it left off.
    Desktop notifications are best-effort (macOS/Linux only, silently skipped
    when no notifier is available, e.g. inside Docker) — `digests/YYYY-MM-DD.md`
    is the reliable per-match record either way.
    """
    settings = load_settings()
    account_id = _require_account(settings)

    stop = {"flag": False}

    def _handle_signal(signum: int, frame: Any) -> None:
        console.print("\n[dim]Stopping after the current match…[/dim]")
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    console.print(
        f"Watching every {settings.poll_interval_minutes} min "
        f"(modes: {', '.join(settings.match_modes)})…"
    )
    while not stop["flag"]:
        with _client(settings) as client, db.session() as conn:
            try:
                _run_watch_cycle(
                    client,
                    conn,
                    account_id,
                    Assets(client),
                    settings,
                    anthropic_client,
                    stop_requested=lambda: stop["flag"],
                )
            except DeadlockAPIError as e:
                console.print(f"[yellow]Poll cycle failed: {e}[/yellow]")

        if once or stop["flag"]:
            break
        # 1s ticks (not one long sleep) so SIGTERM/SIGINT land promptly —
        # matters under `docker stop` / `docker-compose.yml`'s
        # `restart: unless-stopped`, which sends SIGTERM before force-killing.
        for _ in range(settings.poll_interval_minutes * 60):
            if stop["flag"]:
                break
            time.sleep(1)

    console.print("[dim]Stopped.[/dim]")


def _print_matches(entries: list[MatchHistoryEntry], assets: Assets) -> None:
    table = Table(show_header=True, header_style="bold")
    for col in ("match_id", "when", "hero", "mode", "result", "K/D/A", "net worth", "dur"):
        table.add_column(col)
    for e in entries:
        when = _dt.datetime.fromtimestamp(e.start_time).strftime("%Y-%m-%d %H:%M")
        result = "[green]W[/green]" if e.won else "[red]L[/red]"
        if e.abandoned:
            result = "[yellow]abandon[/yellow]"
        table.add_row(
            str(e.match_id),
            when,
            assets.hero_name(e.hero_id),
            e.match_mode_name,
            result,
            e.kda,
            f"{e.net_worth:,}",
            f"{e.match_duration_s // 60}m",
        )
    console.print(table)


if __name__ == "__main__":
    app()

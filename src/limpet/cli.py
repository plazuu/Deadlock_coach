"""``limpet`` command-line interface.

Phase 0/1 surface:
  limpet init                 write config, resolve Steam id, cache assets
  limpet whoami               show resolved account id + current rank
  limpet assets refresh       force-refresh the local hero/item/rank cache
  limpet matches [--limit N]  list recent match history
  limpet fetch <match_id>     fetch + cache full match metadata
  limpet sync                 pull match history into the local db
  limpet analyze <match_id>   features + benchmarks + (with a key) a coaching report
  limpet progress             show the running coaching profile (PROGRESS.md)
"""

from __future__ import annotations

import json
import time
from typing import Annotated

import anthropic
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import paths
from .api.client import DeadlockAPIError, DeadlockClient, MetadataNotReady
from .api.models import MatchHistoryEntry, PlayerRank
from .assets import Assets
from .coach.analyze import CoachError
from .coach.analyze import analyze_match as run_coach
from .coach.briefing import build as build_briefing
from .coach.briefing import estimate_tokens
from .coach.builds import build_item_context
from .coach.focus import reconcile as reconcile_focus_areas
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
from .parse.metadata import PlayerNotInMatch
from .parse.metadata import load as load_match
from .report.markdown import render as render_report
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

        meta = cached_metadata(match_id)
        if meta is None:
            try:
                meta, _path = fetch_metadata(client, match_id)
            except MetadataNotReady as e:
                console.print(f"[yellow]{e}[/yellow] Try again in a few minutes.")
                raise typer.Exit(2) from e
            except DeadlockAPIError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1) from e

        try:
            # Upserts a base `matches` row from the metadata itself, so `analyze`
            # works standalone without a prior `sync` (e.g. on a friend's match).
            upsert_from_metadata(conn, meta, account_id)
        except PlayerNotInMatch as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e

        now = int(time.time())
        raw_path = paths.match_cache_dir(match_id) / "metadata.json"
        conn.execute(
            "UPDATE matches SET raw_meta_path = ?, ingested_at = ? WHERE match_id = ?",
            (str(raw_path), now, match_id),
        )

        view = load_match(meta)
        features = extract_features(view, account_id, Assets(client))
        feats_dict = features.to_dict()

        distributions = None
        try:
            distributions = fetch_hero_distributions(
                client, view.player(account_id).get("hero_id", 0), view.average_badge(account_id)
            )
            attach_benchmarks(feats_dict, distributions)
        except DeadlockAPIError as e:
            console.print(f"[yellow]Benchmarks unavailable, showing raw features: {e}[/yellow]")

        db.save_features(conn, match_id, feats_dict, distributions, now)
        conn.execute("UPDATE matches SET analyzed_at = ? WHERE match_id = ?", (now, match_id))

        if not report:
            console.print_json(json.dumps(feats_dict))
            return

        assets = Assets(client)
        player = view.player(account_id)
        hero_name = assets.hero_name(player.get("hero_id", 0))
        won = bool(view.winning_team is not None and player.get("team") == view.winning_team)
        focus_areas = [
            {"dimension": r["dimension"], "theme": r["theme"], "status": r["status"]}
            for r in db.active_focus_areas(conn)
        ]
        item_builds = build_item_context(view, account_id, assets)
        briefing = build_briefing(
            feats_dict,
            match_id=match_id,
            hero_name=hero_name,
            won=won,
            duration_s=view.duration_s,
            rank_name=assets.rank_name(view.average_badge(account_id)),
            active_focus_areas=focus_areas,
            item_builds=item_builds,
        )
        est_tokens = estimate_tokens(json.dumps(briefing))
        if est_tokens > settings.briefing_token_budget:
            console.print(
                f"[yellow]Briefing is ~{est_tokens} tokens, over the "
                f"{settings.briefing_token_budget} budget — sending anyway.[/yellow]"
            )

        try:
            anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
            coaching_report, usage = run_coach(anthropic_client, briefing, model=settings.model)
        except CoachError as e:
            console.print(f"[red]Coaching report failed: {e}[/red]")
            console.print("[dim]Features + benchmarks were still saved.[/dim]")
            console.print_json(json.dumps(feats_dict))
            return

        markdown = render_report(
            coaching_report,
            match_id=match_id,
            hero_name=hero_name,
            won=won,
            played_at=view.raw.get("start_time", 0),
            duration_s=view.duration_s,
        )
        db.save_report(conn, match_id, settings.model, markdown, coaching_report.model_dump(), now)

        try:
            reconcile_focus_areas(anthropic_client, conn, match_id, coaching_report, now)
        except CoachError as e:
            console.print(f"[yellow]Focus-area tracking skipped this match: {e}[/yellow]")
        regenerate_progress(conn)

    paths.reports_dir().mkdir(parents=True, exist_ok=True)
    report_path = paths.reports_dir() / f"{view.raw.get('start_time', 0)}_{match_id}.md"
    report_path.write_text(markdown)
    console.print(markdown)
    console.print(
        f"\n[dim]Saved -> {report_path}  "
        f"(in {usage.input_tokens} + out {usage.output_tokens} tokens) "
        f"-- progress -> {paths.progress_path()}[/dim]"
    )


@app.command()
def progress() -> None:
    """Show the running coaching profile (regenerated after every `analyze`)."""
    path = paths.progress_path()
    if not path.exists():
        console.print("[dim]No progress tracked yet — run `limpet analyze <match_id>` first.[/dim]")
        return
    console.print(Markdown(path.read_text()))


def _print_matches(entries: list[MatchHistoryEntry], assets: Assets) -> None:
    import datetime as _dt

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

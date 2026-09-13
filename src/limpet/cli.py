"""``limpet`` command-line interface.

Phase 0/1 surface:
  limpet init                 write config, resolve Steam id, cache assets
  limpet whoami               show resolved account id + current rank
  limpet assets refresh       force-refresh the local hero/item/rank cache
  limpet matches [--limit N]  list recent match history
  limpet fetch <match_id>     fetch + cache full match metadata
  limpet sync                 pull match history into the local db
  limpet analyze <match_id>   compute micro/macro features for one match (no LLM yet)
"""

from __future__ import annotations

import json
import time
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import paths
from .api.client import DeadlockAPIError, DeadlockClient, MetadataNotReady
from .api.models import MatchHistoryEntry, PlayerRank
from .assets import Assets
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
    """Show the resolved account id and current rank."""
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
            return
        console.print(f"[green]{len(new)} new match(es):[/green]")
        _print_matches(new[:20], Assets(client))
    if len(new) > 20:
        console.print(f"[dim]…and {len(new) - 20} more.[/dim]")


@app.command()
def matches(
    limit: Annotated[int, typer.Option(help="How many recent matches to show.")] = 15,
) -> None:
    """List recent match history (from the API, not the local db)."""
    settings = load_settings()
    account_id = _require_account(settings)
    with _client(settings) as client:
        raw = client.match_history(account_id)
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
    match_id: Annotated[int, typer.Argument(help="Match id to analyze.")],
) -> None:
    """Compute micro/macro features for one match. No LLM call yet (Phase 3)."""
    settings = load_settings()
    account_id = _require_account(settings)
    meta = cached_metadata(match_id)
    with _client(settings) as client, db.session() as conn:
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

    console.print_json(json.dumps(feats_dict))


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

from pathlib import Path
from typing import Optional

import typer

from fitgrabber.config import PLATFORMS, Config, load_config

app = typer.Typer(help="Collect, organize, and analyze personal fitness data.")


@app.command()
def config(
    data_dir: Optional[Path] = typer.Option(None, help="Set the data directory path"),
    show: bool = typer.Option(False, help="Show current configuration"),
) -> None:
    """Configure fitgrabber settings."""
    cfg = load_config()
    if show:
        typer.echo(f"Data directory: {cfg.data_dir}")
        typer.echo(f"Config file:   {cfg.CONFIG_FILE}" if hasattr(cfg, "CONFIG_FILE") else "")
        for name, settings in cfg.platforms.items():
            typer.echo(f"  {name}: {settings}")
        return
    if data_dir:
        cfg.data_dir = data_dir
    cfg.init_data_dir()
    cfg.save()
    typer.echo(f"Data directory initialized at {cfg.data_dir}")


@app.command()
def sync(
    platform: str = typer.Argument(help=f"Platform to sync: {', '.join(PLATFORMS)} or 'all'"),
) -> None:
    """Download data from a fitness platform."""
    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("Run 'fitgrabber config' first to set up the data directory.", err=True)
        raise typer.Exit(1)

    targets = PLATFORMS if platform == "all" else [platform]
    for t in targets:
        if t not in PLATFORMS:
            typer.echo(f"Unknown platform: {t}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Syncing {t}...")
        _sync_platform(t, cfg)


@app.command()
def status() -> None:
    """Show what data has been downloaded and processed."""
    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("No data directory configured. Run 'fitgrabber config' first.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Data directory: {cfg.data_dir}\n")
    for p in PLATFORMS:
        raw = cfg.raw_dir(p)
        if raw.exists():
            files = list(raw.rglob("*"))
            files = [f for f in files if f.is_file()]
            typer.echo(f"  {p:15s} {len(files):5d} files")

    ind = cfg.processed_individual_dir()
    merged = cfg.processed_merged_dir()
    ind_count = len([f for f in ind.rglob("*") if f.is_file()]) if ind.exists() else 0
    merged_count = len([f for f in merged.rglob("*") if f.is_file()]) if merged.exists() else 0
    typer.echo(f"\n  processed/individual: {ind_count} files")
    typer.echo(f"  processed/merged:    {merged_count} files")


@app.command()
def process() -> None:
    """Run the dedup/merge/clean pipeline on downloaded data."""
    typer.echo("Processing pipeline not yet implemented.")


def _sync_platform(platform: str, cfg: Config) -> None:
    """Dispatch to platform-specific sync module."""
    # Lazy imports to avoid loading all platform deps at startup
    if platform == "garmin":
        from fitgrabber.platforms.garmin import sync as do_sync
    elif platform == "strava":
        from fitgrabber.platforms.strava import sync as do_sync
    elif platform == "myfitnesspal":
        from fitgrabber.platforms.myfitnesspal import sync as do_sync
    elif platform in ("coros", "suunto", "stryd", "sporttracks", "manual"):
        from fitgrabber.platforms.manual import sync as do_sync
    else:
        typer.echo(f"Sync not implemented for {platform}")
        return
    files = do_sync(cfg, platform)
    typer.echo(f"  Downloaded {len(files)} files to {cfg.raw_dir(platform)}")

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
def process(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show details per file"),
    after: Optional[str] = typer.Option(None, help="After date (YYYY-MM-DD)"),
    before: Optional[str] = typer.Option(None, help="Before date (YYYY-MM-DD)"),
    force: bool = typer.Option(False, "--force", help="Reprocess all, ignore cache"),
) -> None:
    """Run the dedup/merge/clean pipeline on downloaded data."""
    import json
    from datetime import datetime

    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("Run 'fitgrabber config' first to set up the data directory.", err=True)
        raise typer.Exit(1)

    from fitgrabber.processing.anomaly import detect_anomalies
    from fitgrabber.processing.catalog import (
        _parse_file,
        build_catalog,
        save_catalog,
    )
    from fitgrabber.processing.dedup import find_duplicates
    from fitgrabber.processing.merge import merge_activities

    # Step 1: Build catalog (incremental — skips unchanged files)
    typer.echo("Building activity catalog...")
    if force:
        # Delete existing catalog to force full rebuild
        cat_path = cfg.catalog_path()
        if cat_path.exists():
            cat_path.unlink()
    catalog, activity_cache = build_catalog(cfg)
    save_catalog(cfg, catalog)

    # Filter by date range
    if after or before:
        after_dt = datetime.fromisoformat(after) if after else None
        before_dt = datetime.fromisoformat(before) if before else None
        original = len(catalog)
        catalog = _filter_by_date(catalog, after_dt, before_dt)
        typer.echo(f"  Date filter: {len(catalog)}/{original} activities")

    # Step 2: Find duplicates
    typer.echo("Detecting duplicates...")
    dup_groups = find_duplicates(catalog)
    typer.echo(f"  Found {len(dup_groups)} duplicate groups")

    # Step 3: Determine which entries are unique vs duplicated
    duped_files = {e["source_file"] for group in dup_groups for e in group}
    unique_entries = [e for e in catalog if e["source_file"] not in duped_files]

    # Build set of existing processed files to skip re-processing
    ind_dir = cfg.processed_individual_dir()
    merged_dir = cfg.processed_merged_dir()
    ind_dir.mkdir(parents=True, exist_ok=True)
    merged_dir.mkdir(parents=True, exist_ok=True)
    existing_ts_prefixes: set[str] = set()
    if not force:
        for d in (ind_dir, merged_dir):
            for f in list(d.glob("*.json")) + list(d.glob("*.fit")):
                parts = f.stem.split("_", 2)
                if len(parts) >= 2:
                    existing_ts_prefixes.add(f"{parts[0]}_{parts[1]}")

    def _get_activity(entry: dict) -> object | None:
        """Get activity from cache or parse from file."""
        key = entry["source_file"]
        if key in activity_cache:
            return activity_cache[key]
        return _parse_file(Path(key), entry["source_platform"])

    def _ts_prefix(entry: dict) -> str:
        """Extract timestamp prefix for matching existing output files."""
        ts = entry.get("start_time", "")
        if ts:
            from datetime import datetime as dt

            try:
                return dt.fromisoformat(ts).strftime("%Y%m%d_%H%M%S")
            except ValueError:
                pass
        return ""

    # Step 4: Process unique activities — parse, detect anomalies, save
    typer.echo("Processing individual activities...")
    ind_count = 0
    skipped = 0
    all_anomalies: list[dict] = []

    for entry in unique_entries:
        if _ts_prefix(entry) in existing_ts_prefixes:
            skipped += 1
            continue
        activity = _get_activity(entry)
        if not activity:
            continue
        if verbose:
            _log_activity(activity)
        anomalies = detect_anomalies(activity)
        _collect_anomalies(all_anomalies, str(activity.source_file), anomalies)
        _save_activity_json(activity, ind_dir, anomalies)
        ind_count += 1

    # Step 5: Merge duplicate groups
    typer.echo("Merging duplicate activities...")
    merged_count = 0

    for group in dup_groups:
        # Check if already processed (use first entry's timestamp for filename)
        rep = group[0]
        if _ts_prefix(rep) in existing_ts_prefixes:
            skipped += 1
            continue

        activities = []
        for entry in group:
            a = _get_activity(entry)
            if a:
                activities.append(a)
        if len(activities) < 2:
            if activities:
                if verbose:
                    _log_activity(activities[0])
                anomalies = detect_anomalies(activities[0])
                _collect_anomalies(all_anomalies, str(activities[0].source_file), anomalies)
                _save_activity_json(activities[0], ind_dir, anomalies)
                ind_count += 1
            continue

        if verbose:
            sources = [f"{a.source_platform}:{Path(str(a.source_file)).name}" for a in activities]
            typer.echo(f"  Merging: {', '.join(sources)}")
        merged = merge_activities(activities, verbose=verbose)
        if verbose:
            _log_activity(merged, prefix="    Result")
        anomalies = detect_anomalies(merged)
        label = "merged:" + ",".join(str(a.source_file) for a in activities)
        _collect_anomalies(all_anomalies, label, anomalies)
        _save_merged_fit(merged, merged_dir)
        merged_count += 1

    # Step 6: Save anomaly report
    if all_anomalies:
        report_path = cfg.data_dir / "processed" / "anomalies.json"
        report_path.write_text(json.dumps(all_anomalies, indent=2, default=str))
        warnings = sum(1 for a in all_anomalies if a["severity"] == "warning")
        errors = sum(1 for a in all_anomalies if a["severity"] == "error")
        typer.echo(f"  Anomalies: {errors} errors, {warnings} warnings → {report_path}")

    skip_msg = f", {skipped} skipped" if skipped else ""
    typer.echo(f"\nDone: {ind_count} individual + {merged_count} merged{skip_msg}")


def _filter_by_date(
    catalog: list[dict],
    after: object,
    before: object,
) -> list[dict]:
    from datetime import datetime

    filtered = []
    for e in catalog:
        st = e.get("start_time")
        if not st:
            continue
        t = datetime.fromisoformat(st).replace(tzinfo=None)
        if after and t < after:
            continue
        if before and t >= before:
            continue
        filtered.append(e)
    return filtered


def _log_activity(activity: object, prefix: str = "  ") -> None:
    dist = f"{activity.total_distance / 1000:.1f}km" if activity.total_distance else "?km"
    dur = f"{activity.total_duration / 60:.0f}min" if activity.total_duration else "?min"
    name = activity.name or Path(str(activity.source_file)).name
    typer.echo(f"{prefix} {activity.sport:12s} {dist:>8s} {dur:>6s}  {name}")


def _collect_anomalies(dest: list[dict], file_label: str, anomalies: list) -> None:
    for a in anomalies:
        dest.append(
            {
                "file": file_label,
                "index": a.index,
                "reason": a.reason,
                "severity": a.severity,
            }
        )


def _save_merged_fit(activity: object, dest_dir: Path) -> Path:
    """Write a merged Activity as a FIT file."""
    from fitgrabber.export.fit_writer import write_fit

    ts = activity.start_time.strftime("%Y%m%d_%H%M%S") if activity.start_time else "unknown"
    name_slug = activity.sport or "activity"
    filename = f"{ts}_{name_slug}_merged.fit"
    path = dest_dir / filename
    write_fit(activity, path)
    return path


def _save_activity_json(
    activity: object,
    dest_dir: Path,
    anomalies: list | None = None,
) -> Path:
    """Serialize an Activity to a JSON file in dest_dir."""
    import json

    ts = activity.start_time.strftime("%Y%m%d_%H%M%S") if activity.start_time else "unknown"
    name_slug = activity.sport or "activity"
    filename = f"{ts}_{name_slug}_{activity.source_platform}.json"
    path = dest_dir / filename

    data = {
        "source_file": str(activity.source_file),
        "source_platform": activity.source_platform,
        "sport": activity.sport,
        "start_time": str(activity.start_time) if activity.start_time else None,
        "end_time": str(activity.end_time) if activity.end_time else None,
        "total_distance": activity.total_distance,
        "total_duration": activity.total_duration,
        "total_calories": activity.total_calories,
        "avg_heart_rate": activity.avg_heart_rate,
        "max_heart_rate": activity.max_heart_rate,
        "avg_speed": activity.avg_speed,
        "avg_cadence": activity.avg_cadence,
        "avg_power": activity.avg_power,
        "name": activity.name,
        "metadata": activity.metadata,
        "num_track_points": len(activity.track_points),
        "track_points": [
            {
                "timestamp": str(p.timestamp),
                "latitude": p.latitude,
                "longitude": p.longitude,
                "altitude": p.altitude,
                "heart_rate": p.heart_rate,
                "cadence": p.cadence,
                "speed": p.speed,
                "power": p.power,
                "temperature": p.temperature,
                "distance": p.distance,
            }
            for p in activity.track_points
        ],
    }
    if anomalies:
        data["anomalies"] = [
            {"index": a.index, "reason": a.reason, "severity": a.severity} for a in anomalies
        ]

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


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

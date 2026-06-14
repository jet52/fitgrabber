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
    total_new = 0
    for t in targets:
        if t not in PLATFORMS:
            typer.echo(f"Unknown platform: {t}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Syncing {t}...")
        total_new += _sync_platform(t, cfg)

    if total_new > 0:
        typer.echo(f"\nProcessing {total_new} new activities...")
        _run_process(cfg)


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
def web(
    port: int = typer.Option(8741, help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    debug: bool = typer.Option(False, help="Enable debug mode"),
    no_browser: bool = typer.Option(False, help="Don't open browser automatically"),
) -> None:
    """Launch the web UI for browsing fitness data."""
    import threading
    import webbrowser

    from fitgrabber.web.app import create_app

    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("Run 'fitgrabber config' first to set up the data directory.", err=True)
        raise typer.Exit(1)

    flask_app = create_app(cfg)
    url = f"http://{host}:{port}"
    typer.echo(f"Starting fitgrabber web UI at {url}")

    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    flask_app.run(host=host, port=port, debug=debug)


@app.command()
def backfill_strava(
    ctx: typer.Context,
) -> None:
    """Backfill summary metrics + device info into existing Strava JSON files."""
    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("Run 'fitgrabber config' first.", err=True)
        raise typer.Exit(1)

    from fitgrabber.platforms.strava import backfill_summary

    updated = backfill_summary(cfg)
    if updated:
        typer.echo("Run 'fitgrabber process --force' to rebuild with updated data.")


@app.command()
def process(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show details per file"),
    after: Optional[str] = typer.Option(None, help="After date (YYYY-MM-DD)"),
    before: Optional[str] = typer.Option(None, help="Before date (YYYY-MM-DD)"),
    force: bool = typer.Option(False, "--force", help="Reprocess all, ignore cache"),
) -> None:
    """Run the dedup/merge/clean pipeline on downloaded data."""
    from datetime import datetime

    cfg = load_config()
    if not cfg.data_dir.exists():
        typer.echo("Run 'fitgrabber config' first to set up the data directory.", err=True)
        raise typer.Exit(1)

    after_dt = datetime.fromisoformat(after) if after else None
    before_dt = datetime.fromisoformat(before) if before else None
    _run_process(cfg, verbose=verbose, force=force, after=after_dt, before=before_dt)


def _run_process(
    cfg: Config,
    verbose: bool = False,
    force: bool = False,
    after: object = None,
    before: object = None,
) -> None:
    """Core processing pipeline used by both `process` and `sync` commands."""
    import json

    from fitgrabber.processing.anomaly import detect_anomalies
    from fitgrabber.processing.catalog import (
        _parse_file,
        build_catalog,
        save_catalog,
    )
    from fitgrabber.processing.dedup import find_duplicates
    from fitgrabber.processing.manifest import (
        load_manifest,
        manifest_path,
        save_manifest,
    )
    from fitgrabber.processing.merge import merge_activities

    # Step 1: Build catalog (incremental — skips unchanged files)
    typer.echo("Building activity catalog...")
    if force:
        cat_path = cfg.catalog_path()
        if cat_path.exists():
            cat_path.unlink()
        man_path = manifest_path(cfg)
        if man_path.exists():
            man_path.unlink()
    catalog, activity_cache = build_catalog(cfg)
    save_catalog(cfg, catalog)

    # Filter by date range
    if after or before:
        original = len(catalog)
        catalog = _filter_by_date(catalog, after, before)
        typer.echo(f"  Date filter: {len(catalog)}/{original} activities")

    # Step 2: Find duplicates
    typer.echo("Detecting duplicates...")
    dup_groups = find_duplicates(catalog)
    typer.echo(f"  Found {len(dup_groups)} duplicate groups")

    # Step 3: Determine which entries are unique vs duplicated
    duped_files = {e["source_file"] for group in dup_groups for e in group}
    unique_entries = [e for e in catalog if e["source_file"] not in duped_files]

    # Ensure output directories exist
    ind_dir = cfg.processed_individual_dir()
    merged_dir = cfg.processed_merged_dir()
    ind_dir.mkdir(parents=True, exist_ok=True)
    merged_dir.mkdir(parents=True, exist_ok=True)

    # Clean up stale individual files whose activities are now in merge groups
    if force:
        _clean_stale_individuals(ind_dir, dup_groups, verbose)

    # Load manifest for incremental processing
    manifest = load_manifest(cfg) if not force else {}

    def _get_activity(entry: dict) -> object | None:
        key = entry["source_file"]
        if key in activity_cache:
            return activity_cache[key]
        return _parse_file(Path(key), entry["source_platform"])

    def _ts_prefix(entry: dict) -> str:
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
    new_manifest: dict[str, dict] = {}

    for entry in unique_entries:
        ts = _ts_prefix(entry)
        sources = {entry["source_file"]}
        existing = manifest.get(ts)
        if existing and set(existing["source_files"]) == sources:
            new_manifest[ts] = existing
            skipped += 1
            continue
        if existing:
            _remove_output(existing["output_file"])
            if verbose:
                typer.echo(f"  Removed stale: {existing['output_file']}")
        activity = _get_activity(entry)
        if not activity:
            continue
        if verbose:
            _log_activity(activity)
        anomalies = detect_anomalies(activity)
        _collect_anomalies(all_anomalies, str(activity.source_file), anomalies)
        out_path = _save_activity_json(activity, ind_dir, anomalies)
        new_manifest[ts] = {"output_file": str(out_path), "source_files": list(sources)}
        ind_count += 1

    # Step 5: Merge duplicate groups
    typer.echo("Merging duplicate activities...")
    merged_count = 0

    for group in dup_groups:
        rep = group[0]
        ts = _ts_prefix(rep)
        sources = {e["source_file"] for e in group}
        existing = manifest.get(ts)
        if existing and set(existing["source_files"]) == sources:
            new_manifest[ts] = existing
            skipped += 1
            continue
        if existing:
            _remove_output(existing["output_file"])
            if verbose:
                typer.echo(f"  Removed stale: {existing['output_file']}")

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
                out_path = _save_activity_json(activities[0], ind_dir, anomalies)
                new_manifest[ts] = {
                    "output_file": str(out_path),
                    "source_files": list(sources),
                }
                ind_count += 1
            continue

        if verbose:
            src_names = [f"{a.source_platform}:{Path(str(a.source_file)).name}" for a in activities]
            typer.echo(f"  Merging: {', '.join(src_names)}")
        merged = merge_activities(activities, verbose=verbose)
        if verbose:
            _log_activity(merged, prefix="    Result")
        anomalies = detect_anomalies(merged)
        label = "merged:" + ",".join(str(a.source_file) for a in activities)
        _collect_anomalies(all_anomalies, label, anomalies)
        out_path = _save_merged_fit(merged, merged_dir)
        new_manifest[ts] = {"output_file": str(out_path), "source_files": list(sources)}
        merged_count += 1

    # Step 6: Save anomaly report
    if all_anomalies:
        report_path = cfg.data_dir / "processed" / "anomalies.json"
        report_path.write_text(json.dumps(all_anomalies, indent=2, default=str))
        warnings = sum(1 for a in all_anomalies if a["severity"] == "warning")
        errors = sum(1 for a in all_anomalies if a["severity"] == "error")
        typer.echo(f"  Anomalies: {errors} errors, {warnings} warnings → {report_path}")

    # Carry forward manifest entries for activities outside the processed window
    # so a date-scoped run doesn't drop them from the authoritative manifest.
    if after or before:
        for ts, info in manifest.items():
            if ts not in new_manifest and not _ts_in_window(ts, after, before):
                new_manifest[ts] = info

    # Step 7: Save manifest
    save_manifest(cfg, new_manifest)

    # Step 7.5: Prune output files no longer referenced by the manifest
    _prune_orphan_outputs(cfg, new_manifest, verbose, after, before)

    # Step 8: Touch marker so the web UI auto-refreshes
    (cfg.data_dir / "processed" / ".last_processed").write_text("")

    skip_msg = f", {skipped} skipped" if skipped else ""
    typer.echo(f"\nDone: {ind_count} individual + {merged_count} merged{skip_msg}")


def _prune_orphan_outputs(
    cfg: Config,
    manifest: dict[str, dict],
    verbose: bool,
    after: object = None,
    before: object = None,
) -> None:
    """Delete processed outputs (and merged sidecars) not referenced by the manifest.

    Reprocessing can leave orphans when an activity's timestamp prefix shifts or a
    source is removed. The manifest is the authoritative set of intended outputs.

    When a date filter is active, only files within the processed window are
    candidates for pruning — out-of-window outputs weren't reprocessed this run,
    so the (window-only) manifest doesn't reference them and they must be kept.
    """
    referenced = {Path(info["output_file"]) for info in manifest.values()}
    removed = 0
    for d in (cfg.processed_merged_dir(), cfg.processed_individual_dir()):
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.name.endswith(".meta.json"):
                continue  # sidecar removed alongside its .fit
            if f.suffix not in (".fit", ".json"):
                continue
            if (after or before) and not _ts_in_window(f.name, after, before):
                continue  # outside the processed window — not a prune candidate
            if f not in referenced:
                _remove_output(str(f))
                removed += 1
                if verbose:
                    typer.echo(f"  Pruned orphan: {f.name}")
    if removed:
        typer.echo(f"  Pruned {removed} orphaned output file(s)")


def _ts_in_window(name: str, after: object, before: object) -> bool:
    """Whether an output file's leading YYYYMMDD_HHMMSS timestamp is in [after, before)."""
    from datetime import datetime

    try:
        t = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return False  # unparseable prefix — leave it alone
    if after and t < after:
        return False
    if before and t >= before:
        return False
    return True


def _clean_stale_individuals(ind_dir: Path, dup_groups: list[list[dict]], verbose: bool) -> None:
    """Remove individual files whose activities now belong to a merge group.

    When --force reprocesses, an activity that was previously unique (saved as
    individual) may now have a matching source and belong to a dup group.
    The merge produces a new merged file, but the old individual lingers.
    """
    from datetime import datetime, timedelta

    if not ind_dir.exists():
        return

    # Collect all timestamps from dup groups (these will become merged files)
    dup_timestamps: list[datetime] = []
    for group in dup_groups:
        for entry in group:
            ts = entry.get("start_time")
            if ts:
                try:
                    dup_timestamps.append(datetime.fromisoformat(ts))
                except ValueError:
                    pass

    if not dup_timestamps:
        return

    tolerance = timedelta(minutes=5)
    removed = 0
    for f in list(ind_dir.iterdir()):
        if f.suffix != ".json":
            continue
        parts = f.stem.split("_", 2)
        if len(parts) < 2:
            continue
        try:
            file_ts = datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        # Check if this individual's timestamp is close to any dup group entry
        for dt in dup_timestamps:
            dt_naive = dt.replace(tzinfo=None)
            if abs(file_ts - dt_naive) <= tolerance:
                if verbose:
                    typer.echo(f"  Removing stale individual: {f.name}")
                f.unlink()
                removed += 1
                break

    if removed:
        typer.echo(f"  Cleaned {removed} stale individual file(s)")


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


def _remove_output(output_file: str) -> None:
    """Delete a processed output file and its sidecar (if a merged FIT)."""
    path = Path(output_file)
    path.unlink(missing_ok=True)
    if path.suffix == ".fit":
        from fitgrabber.export.sidecar import sidecar_path

        sidecar_path(path).unlink(missing_ok=True)


def _save_merged_fit(activity: object, dest_dir: Path) -> Path:
    """Write a merged Activity as a FIT file plus a provenance/R-R sidecar."""
    from fitgrabber.export.fit_writer import write_fit
    from fitgrabber.export.sidecar import write_sidecar

    ts = activity.start_time.strftime("%Y%m%d_%H%M%S") if activity.start_time else "unknown"
    name_slug = activity.sport or "activity"
    filename = f"{ts}_{name_slug}_merged.fit"
    path = dest_dir / filename
    write_fit(activity, path)
    write_sidecar(activity, path)
    return path


def _save_activity_json(
    activity: object,
    dest_dir: Path,
    anomalies: list | None = None,
) -> Path:
    """Serialize an Activity to a JSON file in dest_dir."""
    import json

    from fitgrabber.processing.merge import fill_missing_summary

    fill_missing_summary(activity)

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
        "power_source": activity.power_source,
        "power_source_alt": activity.power_source_alt,
        "hr_source": activity.hr_source,
        "hr_detail": activity.hr_detail,
        "rr_ms": [round(v * 1000) for v in activity.rr_intervals]
        if activity.rr_intervals
        else None,
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


def _sync_platform(platform: str, cfg: Config) -> int:
    """Dispatch to platform-specific sync module. Returns number of new files."""
    # Lazy imports to avoid loading all platform deps at startup
    if platform == "garmin":
        from fitgrabber.platforms.garmin import sync as do_sync
    elif platform == "garmin-health":
        from fitgrabber.platforms.garmin_health import sync as do_sync
    elif platform == "strava":
        from fitgrabber.platforms.strava import sync as do_sync
    elif platform in ("coros", "suunto", "stryd", "sporttracks", "manual"):
        from fitgrabber.platforms.manual import sync as do_sync
    else:
        typer.echo(f"Sync not implemented for {platform}")
        return 0
    files = do_sync(cfg, platform)
    typer.echo(f"  Downloaded {len(files)} files to {cfg.raw_dir(platform)}")
    return len(files)

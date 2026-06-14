import json
import os
import tempfile
import zipfile
from pathlib import Path

import typer

from fitgrabber.config import NON_ACTIVITY_PLATFORMS, PLATFORMS, Config
from fitgrabber.parsers.models import Activity

SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".csv", ".json", ".zip"}


def build_catalog(cfg: Config, progress: bool = True) -> tuple[list[dict], dict[str, Activity]]:
    """Scan raw/ directories and build an activity index incrementally.

    Returns (catalog_entries, activity_cache) where activity_cache maps
    source_file paths to parsed Activity objects for newly parsed files.
    """
    existing = _index_existing_catalog(cfg)
    entries: list[dict] = []
    cache: dict[str, Activity] = {}
    new = 0
    cached = 0
    errors = 0

    for platform in PLATFORMS:
        if platform in NON_ACTIVITY_PLATFORMS:
            continue  # wellness/non-activity data — kept in raw/, not cataloged
        raw_dir = cfg.raw_dir(platform)
        if not raw_dir.exists():
            continue
        files = sorted(
            f
            for f in raw_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        for f in files:
            key = str(f)
            mtime = os.path.getmtime(f)
            prev = existing.pop(key, None)

            if prev and prev.get("file_mtime") == mtime:
                entries.append(prev)
                cached += 1
                continue

            activity = _parse_file(f, platform)
            if activity:
                entry = _activity_to_entry(activity, mtime)
                entries.append(entry)
                cache[key] = activity
                new += 1
            else:
                errors += 1

    removed = len(existing)
    if progress:
        typer.echo(
            f"  Cataloged {len(entries)} activities"
            f" ({new} new, {cached} cached, {removed} removed,"
            f" {errors} skipped)"
        )
    return entries, cache


def _index_existing_catalog(cfg: Config) -> dict[str, dict]:
    """Load existing catalog indexed by source_file for fast lookup."""
    entries = load_catalog(cfg)
    return {e["source_file"]: e for e in entries}


def save_catalog(cfg: Config, entries: list[dict]) -> None:
    path = cfg.catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, default=str))


def load_catalog(cfg: Config) -> list[dict]:
    path = cfg.catalog_path()
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _parse_file(filepath: Path, platform: str) -> Activity | None:
    ext = filepath.suffix.lower()
    try:
        if ext == ".zip":
            return _parse_zip(filepath, platform)
        if ext == ".json":
            return _parse_strava_json(filepath, platform)
        if ext == ".fit":
            from fitgrabber.parsers.fit_parser import parse
        elif ext == ".gpx":
            from fitgrabber.parsers.gpx_parser import parse
        elif ext == ".tcx":
            from fitgrabber.parsers.tcx_parser import parse
        elif ext == ".csv":
            from fitgrabber.parsers.csv_parser import parse
        else:
            return None
        return parse(filepath, platform)
    except Exception:
        return None


def _parse_zip(filepath: Path, platform: str) -> Activity | None:
    """Extract FIT file from a zip and parse it."""
    with zipfile.ZipFile(filepath) as zf:
        fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
        if not fit_names:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(zf.extract(fit_names[0], tmp))
            from fitgrabber.parsers.fit_parser import parse

            activity = parse(extracted, platform)
            activity.source_file = filepath
            return activity


def _parse_strava_json(filepath: Path, platform: str) -> Activity | None:
    """Parse a Strava JSON activity file into an Activity."""
    from datetime import datetime, timedelta, timezone

    from fitgrabber.parsers.models import TrackPoint

    data = json.loads(filepath.read_text())
    # Metadata may be at top level or nested under "metadata"
    meta = data.get("metadata", data)
    streams = data.get("streams", {})

    start = None
    start_raw = meta.get("start_date")
    if start_raw:
        start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))

    # Build track points from streams
    points: list[TrackPoint] = []
    time_stream = streams.get("time", [])
    latlng = streams.get("latlng", [])
    alt = streams.get("altitude", [])
    hr = streams.get("heartrate", [])
    cadence = streams.get("cadence", [])
    power = streams.get("watts", [])
    temp = streams.get("temp", streams.get("temperature", []))
    dist = streams.get("distance", [])
    speed = streams.get("velocity_smooth", [])

    for i, t in enumerate(time_stream):
        ts = start + timedelta(seconds=t) if start else datetime(2000, 1, 1, tzinfo=timezone.utc)
        lat = latlng[i][0] if i < len(latlng) else None
        lon = latlng[i][1] if i < len(latlng) else None
        points.append(
            TrackPoint(
                timestamp=ts,
                latitude=lat,
                longitude=lon,
                altitude=alt[i] if i < len(alt) else None,
                heart_rate=hr[i] if i < len(hr) else None,
                cadence=cadence[i] if i < len(cadence) else None,
                speed=speed[i] if i < len(speed) else None,
                power=power[i] if i < len(power) else None,
                temperature=temp[i] if i < len(temp) else None,
                distance=dist[i] if i < len(dist) else None,
            )
        )

    sport = _parse_strava_sport(meta)
    original_device = _detect_strava_device(data)
    metadata = {}
    if original_device:
        metadata["device_name"] = data.get("device_name", "")
        metadata["original_platform"] = original_device
    # Strava can't tell us the sensor type; label power provenance by the
    # uploading device when a watts stream is present, else None.
    power_source = (
        (original_device or "strava") if any(p.power is not None for p in points) else None
    )
    return Activity(
        source_file=filepath,
        source_platform=platform,
        sport=sport,
        start_time=start,
        end_time=points[-1].timestamp if points else None,
        track_points=points,
        total_distance=meta.get("distance"),
        total_duration=meta.get("elapsed_time") or meta.get("moving_time"),
        total_calories=meta.get("calories"),
        avg_heart_rate=meta.get("average_heartrate"),
        max_heart_rate=meta.get("max_heartrate"),
        avg_speed=meta.get("average_speed"),
        power_source=power_source,
        name=meta.get("name", ""),
        metadata=metadata,
    )


def _detect_strava_device(data: dict) -> str | None:
    """Detect the original recording device/platform from Strava metadata.

    Returns a platform name like "garmin", "coros", "peloton", etc.,
    or None if it appears to be a native Strava recording.
    """
    device_name = str(data.get("device_name") or "").lower()
    external_id = str(data.get("external_id") or "").lower()

    # Check external_id first (most reliable)
    if "garmin_push" in external_id or "garmin_ping" in external_id:
        return "garmin"

    # Check device_name
    device_map = {
        "garmin": "garmin",
        "coros": "coros",
        "suunto": "suunto",
        "peloton": "peloton",
        "wahoo": "wahoo",
        "polar": "polar",
        "amazfit": "amazfit",
        "samsung": "samsung",
        "apple watch": "apple",
        "zwift": "zwift",
        "stryd": "stryd",
    }
    for keyword, platform in device_map.items():
        if keyword in device_name:
            return platform

    # Native Strava app recordings
    if "strava" in device_name:
        return None

    return None


def _parse_strava_sport(meta: dict) -> str:
    """Extract sport from Strava metadata, handling Pydantic repr strings."""
    import re

    raw = meta.get("type") or meta.get("sport_type") or "unknown"
    raw = str(raw)
    # Handle "root='Run'" style from stravalib pydantic models
    m = re.match(r"root='(.+)'", raw)
    if m:
        raw = m.group(1)
    return raw.lower()


def _activity_to_entry(a: Activity, mtime: float | None = None) -> dict:
    return {
        "source_file": str(a.source_file),
        "source_platform": a.source_platform,
        "sport": a.sport,
        "start_time": str(a.start_time) if a.start_time else None,
        "end_time": str(a.end_time) if a.end_time else None,
        "total_distance": a.total_distance,
        "total_duration": a.total_duration,
        "total_calories": a.total_calories,
        "avg_heart_rate": a.avg_heart_rate,
        "max_heart_rate": a.max_heart_rate,
        "avg_speed": a.avg_speed,
        "avg_cadence": a.avg_cadence,
        "avg_power": a.avg_power,
        "power_source": a.power_source,
        "hr_source": a.hr_source,
        "hr_detail": a.hr_detail,
        "name": a.name,
        "num_track_points": len(a.track_points),
        "file_mtime": mtime,
    }

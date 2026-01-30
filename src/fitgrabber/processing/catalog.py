import json
import tempfile
import zipfile
from pathlib import Path

import typer

from fitgrabber.config import PLATFORMS, Config
from fitgrabber.parsers.models import Activity

SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".csv", ".json", ".zip"}


def build_catalog(cfg: Config, progress: bool = True) -> list[dict]:
    """Scan raw/ directories and build an activity index."""
    entries: list[dict] = []
    errors = 0
    for platform in PLATFORMS:
        raw_dir = cfg.raw_dir(platform)
        if not raw_dir.exists():
            continue
        files = sorted(
            f
            for f in raw_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        for f in files:
            activity = _parse_file(f, platform)
            if activity:
                entries.append(_activity_to_entry(activity))
            else:
                errors += 1
    if progress:
        typer.echo(f"  Cataloged {len(entries)} activities ({errors} files skipped)")
    return entries


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
        name=meta.get("name", ""),
    )


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


def _activity_to_entry(a: Activity) -> dict:
    return {
        "source_file": str(a.source_file),
        "source_platform": a.source_platform,
        "sport": a.sport,
        "start_time": str(a.start_time) if a.start_time else None,
        "end_time": str(a.end_time) if a.end_time else None,
        "total_distance": a.total_distance,
        "total_duration": a.total_duration,
        "total_calories": a.total_calories,
        "name": a.name,
        "num_track_points": len(a.track_points),
    }

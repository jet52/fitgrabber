import json
from pathlib import Path

from fitgrabber.config import PLATFORMS, Config
from fitgrabber.parsers.models import Activity

SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".csv"}


def build_catalog(cfg: Config) -> list[dict]:
    """Scan raw/ directories and build an activity index."""
    entries: list[dict] = []
    for platform in PLATFORMS:
        raw_dir = cfg.raw_dir(platform)
        if not raw_dir.exists():
            continue
        for f in sorted(raw_dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            activity = _parse_file(f, platform)
            if activity:
                entries.append(_activity_to_entry(activity))
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
        "num_track_points": len(a.track_points),
    }

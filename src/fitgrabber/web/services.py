"""Data loading services for the web UI."""

import json
from datetime import datetime
from pathlib import Path

from fitgrabber.config import Config


def _activity_from_fit(filepath: Path) -> dict | None:
    """Parse a processed FIT file into a summary dict (no track points)."""
    from fitgrabber.processing.catalog import _parse_file

    a = _parse_file(filepath, "merged")
    if not a:
        return None
    return {
        "id": filepath.stem,
        "source_file": str(filepath),
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
        "name": a.name,
        "num_track_points": len(a.track_points),
    }


def _activity_from_json(filepath: Path) -> dict | None:
    """Load a processed individual JSON into a summary dict (no track points)."""
    try:
        data = json.loads(filepath.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    data["id"] = filepath.stem
    data.pop("track_points", None)
    data.pop("metadata", None)
    return data


_processed_cache: dict[str, list[dict]] | None = None


def get_processed_activities(cfg: Config, force_reload: bool = False) -> list[dict]:
    """Load all processed activities (merged + individual, deduplicated).

    Merged activities take priority — if a timestamp prefix exists in merged/,
    skip any individual/ file with the same prefix.
    """
    global _processed_cache
    if _processed_cache is not None and not force_reload:
        return _processed_cache.get("activities", [])

    activities: list[dict] = []
    seen_prefixes: set[str] = set()

    # Merged FIT files first (higher priority)
    merged_dir = cfg.processed_merged_dir()
    if merged_dir.exists():
        for f in sorted(merged_dir.iterdir()):
            if f.suffix != ".fit":
                continue
            parts = f.stem.split("_", 2)
            prefix = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else f.stem
            seen_prefixes.add(prefix)
            entry = _activity_from_fit(f)
            if entry:
                activities.append(entry)

    # Individual JSON files (skip if merged version exists)
    ind_dir = cfg.processed_individual_dir()
    if ind_dir.exists():
        for f in sorted(ind_dir.iterdir()):
            if f.suffix != ".json":
                continue
            parts = f.stem.split("_", 2)
            prefix = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else f.stem
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
            entry = _activity_from_json(f)
            if entry:
                activities.append(entry)

    _processed_cache = {"activities": activities}
    return activities


def invalidate_cache() -> None:
    global _processed_cache
    _processed_cache = None


def get_activity_detail(cfg: Config, activity_id: str) -> dict | None:
    """Load full activity detail by stem ID."""
    # Check individual JSON first (has track points already serialized)
    ind_dir = cfg.processed_individual_dir()
    if ind_dir.exists():
        for f in ind_dir.iterdir():
            if f.stem == activity_id and f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    data["id"] = f.stem
                    return data
                except (json.JSONDecodeError, OSError):
                    pass

    # Check merged FIT files (need to parse fully)
    merged_dir = cfg.processed_merged_dir()
    if merged_dir.exists():
        for f in merged_dir.iterdir():
            if f.stem == activity_id and f.suffix == ".fit":
                return _parse_fit_detail(f)

    # Fallback: prefix match (e.g. activity_id is timestamp prefix)
    for d in (merged_dir, ind_dir):
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.name.startswith(activity_id):
                continue
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    data["id"] = f.stem
                    return data
                except (json.JSONDecodeError, OSError):
                    continue
            if f.suffix == ".fit":
                return _parse_fit_detail(f)
    return None


def _parse_fit_detail(filepath: Path) -> dict | None:
    """Parse a FIT file into full detail dict with track points and laps."""
    from fitgrabber.processing.catalog import _parse_file

    a = _parse_file(filepath, "merged")
    if not a:
        return None
    return {
        "id": filepath.stem,
        "source_file": str(filepath),
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
        "name": a.name,
        "num_track_points": len(a.track_points),
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
            for p in a.track_points
        ],
        "laps": [
            {
                "start_time": str(lap.start_time),
                "end_time": str(lap.end_time),
                "total_distance": lap.total_distance,
                "total_duration": lap.total_duration,
                "avg_heart_rate": lap.avg_heart_rate,
                "max_heart_rate": lap.max_heart_rate,
                "avg_speed": lap.avg_speed,
                "avg_cadence": lap.avg_cadence,
                "avg_power": lap.avg_power,
                "lap_trigger": lap.lap_trigger,
                "intensity": lap.intensity,
                "sport": lap.sport,
            }
            for lap in a.laps
        ],
    }


def get_dashboard_stats(activities: list[dict]) -> dict:
    """Compute dashboard summary stats from processed activities."""
    from collections import defaultdict
    from datetime import timedelta

    now = datetime.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    stats: dict = {
        "total": len(activities),
        "week": {"count": 0, "distance": 0.0, "duration": 0.0, "by_sport": defaultdict(int)},
        "month": {"count": 0, "distance": 0.0, "duration": 0.0, "by_sport": defaultdict(int)},
        "year": {"count": 0, "distance": 0.0, "duration": 0.0, "by_sport": defaultdict(int)},
        "recent": [],
        "sports": defaultdict(int),
    }

    sorted_activities = sorted(
        activities, key=lambda e: e.get("start_time") or "", reverse=True
    )
    stats["recent"] = sorted_activities[:10]

    for entry in activities:
        sport = entry.get("sport", "unknown")
        stats["sports"][sport] += 1
        ts = entry.get("start_time")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts).replace(tzinfo=None)
        except ValueError:
            continue
        dist = entry.get("total_distance") or 0
        dur = entry.get("total_duration") or 0
        for period, cutoff in [("week", week_ago), ("month", month_ago), ("year", year_start)]:
            if t >= cutoff:
                stats[period]["count"] += 1
                stats[period]["distance"] += dist
                stats[period]["duration"] += dur
                stats[period]["by_sport"][sport] += 1

    return stats

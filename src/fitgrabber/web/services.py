"""Data loading services for the web UI."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fitgrabber.config import Config

# Module-level caches, all cleared together via invalidate_cache().
_cache: dict[str, Any] | None = None
_cache_created: float = 0.0


def _get_cache() -> dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = {}
    return _cache


def _check_stale(cfg: Config) -> None:
    """Auto-invalidate cache if processing has occurred since cache was created."""
    global _cache_created
    if _cache is None:
        return
    marker = cfg.data_dir / "processed" / ".last_processed"
    if marker.exists() and marker.stat().st_mtime > _cache_created:
        invalidate_cache()


def invalidate_cache() -> None:
    global _cache, _cache_created
    _cache = None
    _cache_created = 0.0


def _cached_catalog(cfg: Config) -> list[dict]:
    """Load catalog once per cache lifetime."""
    c = _get_cache()
    if "catalog" not in c:
        from fitgrabber.processing.catalog import load_catalog

        c["catalog"] = load_catalog(cfg)
    return c["catalog"]


def _cached_file_to_prefix(cfg: Config) -> dict[str, str]:
    """Map raw file paths to timestamp prefixes, cached."""
    c = _get_cache()
    if "file_to_prefix" not in c:
        mapping: dict[str, str] = {}
        for entry in _cached_catalog(cfg):
            ts = entry.get("start_time")
            if not ts:
                continue
            try:
                prefix = datetime.fromisoformat(ts).strftime("%Y%m%d_%H%M%S")
            except ValueError:
                continue
            mapping[entry["source_file"]] = prefix
        c["file_to_prefix"] = mapping
    return c["file_to_prefix"]


def _summary_cache_path(cfg: Config) -> Path:
    return cfg.data_dir / "processed" / ".fit_summaries.json"


def _load_summary_cache(cfg: Config) -> dict[str, dict]:
    path = _summary_cache_path(cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {e["id"]: e for e in data}
    except (json.JSONDecodeError, OSError, KeyError):
        return {}


def _save_summary_cache(cfg: Config, summaries: dict[str, dict]) -> None:
    path = _summary_cache_path(cfg)
    path.write_text(json.dumps(list(summaries.values()), indent=2, default=str))


def _activity_from_fit(filepath: Path, summary_cache: dict[str, dict]) -> dict | None:
    """Parse a processed FIT file into a summary dict (no track points).

    Uses a disk cache to avoid re-parsing FIT files on every server start.
    """
    stem = filepath.stem
    mtime = filepath.stat().st_mtime

    cached = summary_cache.get(stem)
    if cached and cached.get("_mtime") == mtime:
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    from fitgrabber.parsers.fit_parser import parse_summary

    try:
        a = parse_summary(filepath, "merged")
    except Exception:
        return None
    entry = {
        "id": stem,
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
    summary_cache[stem] = {**entry, "_mtime": mtime}
    return entry


def _activity_from_json(filepath: Path) -> dict | None:
    """Load a processed individual JSON into a summary dict (no track points)."""
    try:
        data = json.loads(filepath.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    data["id"] = filepath.stem
    data["has_anomalies"] = bool(data.get("anomalies"))
    data.pop("track_points", None)
    data.pop("metadata", None)
    data.pop("anomalies", None)
    return data


def _build_name_lookup(cfg: Config) -> dict[str, str]:
    """Map timestamp prefixes to best activity name from raw catalog."""
    c = _get_cache()
    if "name_lookup" in c:
        return c["name_lookup"]

    catalog = _cached_catalog(cfg)
    names: dict[str, list[tuple[str, str]]] = {}
    for entry in catalog:
        name = entry.get("name", "").strip()
        if not name:
            continue
        ts = entry.get("start_time")
        if not ts:
            continue
        try:
            prefix = datetime.fromisoformat(ts).strftime("%Y%m%d_%H%M%S")
        except ValueError:
            continue
        names.setdefault(prefix, []).append((entry["source_platform"], name))

    result: dict[str, str] = {}
    for prefix, candidates in names.items():
        strava = [n for p, n in candidates if p == "strava"]
        result[prefix] = strava[0] if strava else candidates[0][1]
    c["name_lookup"] = result
    return result


def _build_anomaly_prefixes(cfg: Config) -> set[str]:
    """Load anomalies.json and return timestamp prefixes of anomalous activities."""
    c = _get_cache()
    if "anomaly_prefixes" in c:
        return c["anomaly_prefixes"]

    anom_path = cfg.data_dir / "processed" / "anomalies.json"
    if not anom_path.exists():
        c["anomaly_prefixes"] = set()
        return c["anomaly_prefixes"]
    try:
        anoms = json.loads(anom_path.read_text())
    except (json.JSONDecodeError, OSError):
        c["anomaly_prefixes"] = set()
        return c["anomaly_prefixes"]

    file_to_prefix = _cached_file_to_prefix(cfg)

    prefixes: set[str] = set()
    for a in anoms:
        file_label = a["file"]
        if file_label.startswith("prefix:"):
            prefixes.add(file_label[7:])
        elif file_label.startswith("merged:"):
            paths = file_label[7:].split(",")
            for p in paths:
                if p in file_to_prefix:
                    prefixes.add(file_to_prefix[p])
                    break
        elif file_label in file_to_prefix:
            prefixes.add(file_to_prefix[file_label])
    c["anomaly_prefixes"] = prefixes
    return prefixes


def _prefix_to_ts(prefix: str) -> datetime | None:
    """Parse a YYYYMMDD_HHMMSS prefix into a datetime."""
    try:
        return datetime.strptime(prefix, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _near_any(prefix: str, seen: set[str], tolerance_s: int = 300) -> bool:
    """Check if a timestamp prefix is within tolerance of any seen prefix."""
    ts = _prefix_to_ts(prefix)
    if not ts:
        return False
    for s in seen:
        other = _prefix_to_ts(s)
        if other and abs((ts - other).total_seconds()) <= tolerance_s:
            return True
    return False


def get_processed_activities(cfg: Config, force_reload: bool = False) -> list[dict]:
    """Load all processed activities (merged + individual, deduplicated).

    Merged activities take priority — if a timestamp prefix exists in merged/,
    skip any individual/ file with a similar timestamp (within 5 min tolerance).
    """
    if force_reload:
        invalidate_cache()
    _check_stale(cfg)
    c = _get_cache()
    if "activities" in c:
        return c["activities"]

    name_lookup = _build_name_lookup(cfg)
    anomaly_prefixes = _build_anomaly_prefixes(cfg)
    summary_cache = _load_summary_cache(cfg)
    summary_cache_dirty = False
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
            old_len = len(summary_cache)
            entry = _activity_from_fit(f, summary_cache)
            if len(summary_cache) != old_len:
                summary_cache_dirty = True
            if entry:
                if not entry.get("name") and prefix in name_lookup:
                    entry["name"] = name_lookup[prefix]
                entry["has_anomalies"] = prefix in anomaly_prefixes
                activities.append(entry)

    # Individual JSON files (skip if merged version exists with similar timestamp)
    ind_dir = cfg.processed_individual_dir()
    if ind_dir.exists():
        for f in sorted(ind_dir.iterdir()):
            if f.suffix != ".json":
                continue
            parts = f.stem.split("_", 2)
            prefix = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else f.stem
            if prefix in seen_prefixes or _near_any(prefix, seen_prefixes):
                continue
            seen_prefixes.add(prefix)
            entry = _activity_from_json(f)
            if entry:
                if not entry.get("name") and prefix in name_lookup:
                    entry["name"] = name_lookup[prefix]
                entry["has_anomalies"] = entry.get("has_anomalies") or prefix in anomaly_prefixes
                activities.append(entry)

    if summary_cache_dirty:
        _save_summary_cache(cfg, summary_cache)

    # Normalize sport names and fill derivable stats
    from fitgrabber.processing.sports import normalize_sport

    for a in activities:
        cat, sub = normalize_sport(a.get("sport", "unknown"))
        a["sport"] = cat
        a["sub_sport"] = sub
        # Derive avg_speed from distance/duration if missing
        if not a.get("avg_speed") and a.get("total_distance") and a.get("total_duration"):
            a["avg_speed"] = a["total_distance"] / a["total_duration"]

    c["activities"] = activities
    global _cache_created
    import time

    _cache_created = time.time()
    return activities


def _enrich_name(data: dict, cfg: Config) -> dict:
    """Add best name from raw catalog if the activity has no name."""
    if data.get("name"):
        return data
    parts = data.get("id", "").split("_", 2)
    if len(parts) >= 2:
        prefix = f"{parts[0]}_{parts[1]}"
        names = _build_name_lookup(cfg)
        if prefix in names:
            data["name"] = names[prefix]
    return data


def _fill_missing_stats(data: dict) -> dict:
    """Compute missing summary stats from track points when available."""
    pts = data.get("track_points")
    if not pts or len(pts) < 2:
        return data

    def _avg(field: str) -> float | None:
        vals = [p[field] for p in pts if p.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    if not data.get("avg_heart_rate"):
        data["avg_heart_rate"] = _avg("heart_rate")
    if not data.get("max_heart_rate"):
        vals = [p["heart_rate"] for p in pts if p.get("heart_rate") is not None]
        data["max_heart_rate"] = max(vals) if vals else None
    if not data.get("avg_speed"):
        data["avg_speed"] = _avg("speed")
    if not data.get("avg_cadence"):
        data["avg_cadence"] = _avg("cadence")
    if not data.get("avg_power"):
        data["avg_power"] = _avg("power")

    # Compute total_distance from last track point distance field if missing
    if not data.get("total_distance"):
        dists = [p["distance"] for p in pts if p.get("distance") is not None]
        if dists:
            data["total_distance"] = max(dists)

    # Compute total_duration from timestamps if missing
    if not data.get("total_duration"):
        try:
            t0 = datetime.fromisoformat(pts[0]["timestamp"])
            t1 = datetime.fromisoformat(pts[-1]["timestamp"])
            data["total_duration"] = (t1 - t0).total_seconds()
        except (ValueError, KeyError):
            pass

    return data


def get_activity_detail(cfg: Config, activity_id: str) -> dict | None:
    """Load full activity detail by stem ID."""
    result = _find_activity_detail(cfg, activity_id)
    if result:
        _enrich_name(result, cfg)
        _fill_missing_stats(result)
    return result


def _find_activity_detail(cfg: Config, activity_id: str) -> dict | None:
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

    # Fallback: prefix match
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


COVERAGE_FIELDS = ["GPS", "Heart Rate", "Speed", "Cadence", "Power", "Altitude", "Temperature"]

_FIELD_MAP = {
    "GPS": lambda pts: any(p.get("latitude") for p in pts),
    "Heart Rate": lambda pts: any(p.get("heart_rate") for p in pts),
    "Speed": lambda pts: any(p.get("speed") for p in pts),
    "Cadence": lambda pts: any(p.get("cadence") for p in pts),
    "Power": lambda pts: any(p.get("power") for p in pts),
    "Altitude": lambda pts: any(p.get("altitude") for p in pts),
    "Temperature": lambda pts: any(p.get("temperature") for p in pts),
}


def get_merge_sources(cfg: Config, activity_id: str) -> list[dict]:
    """Find the raw source files that were merged for a given activity.

    Matches by time interval overlap against the raw catalog - any activity
    whose time window overlaps with the merged activity's window is included.
    """
    from fitgrabber.processing.catalog import _parse_file

    # Extract timestamp prefix from activity_id (e.g. "20180331_132316_running_merged")
    parts = activity_id.split("_", 2)
    if len(parts) < 2:
        return []
    ts_prefix = f"{parts[0]}_{parts[1]}"
    activity_ts = _prefix_to_ts(ts_prefix)
    if not activity_ts:
        return []

    # Get merged activity duration from the processed file
    from fitgrabber.parsers.fit_parser import parse_summary

    merged_dir = cfg.processed_merged_dir()
    merged_duration = 0
    for f in merged_dir.iterdir():
        if f.stem.startswith(ts_prefix) and f.suffix == ".fit":
            try:
                activity = parse_summary(f, "merged")
                if activity and activity.total_duration:
                    merged_duration = activity.total_duration
            except Exception:
                pass
            break

    tolerance_s = 300  # 5 minutes tolerance on interval edges

    catalog = _cached_catalog(cfg)
    matches = []
    for entry in catalog:
        ts = entry.get("start_time")
        if not ts:
            continue
        try:
            entry_ts = datetime.fromisoformat(ts).replace(tzinfo=None)
        except ValueError:
            continue

        # Check time interval overlap (not just start time proximity)
        entry_duration = entry.get("total_duration") or 0
        merged_end = activity_ts + timedelta(seconds=merged_duration + tolerance_s)
        entry_end = entry_ts + timedelta(seconds=entry_duration + tolerance_s)
        activity_start_adj = activity_ts - timedelta(seconds=tolerance_s)
        entry_start_adj = entry_ts - timedelta(seconds=tolerance_s)

        # Intervals overlap if each starts before the other ends
        if activity_start_adj <= entry_end and entry_start_adj <= merged_end:
            matches.append(entry)

    sources = []
    for entry in matches:
        filepath = Path(entry["source_file"])
        # Parse full activity to get track point coverage
        activity = _parse_file(filepath, entry["source_platform"])
        pts = []
        if activity:
            pts = [
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
                }
                for p in activity.track_points
            ]

        coverage = {field: check(pts) for field, check in _FIELD_MAP.items()}

        sources.append(
            {
                "source_platform": entry["source_platform"],
                "source_file": entry["source_file"],
                "filename": filepath.name,
                "start_time": entry.get("start_time"),
                "end_time": entry.get("end_time"),
                "total_distance": entry.get("total_distance"),
                "total_duration": entry.get("total_duration"),
                "num_track_points": entry.get("num_track_points", 0),
                "coverage": coverage,
                "track_points": pts,
            }
        )
    return sources


def get_comparison_data(sources: list[dict]) -> dict | None:
    """Build comparison data for overlapping streams across sources."""
    if len(sources) < 2:
        return None

    stream_fields = ["heart_rate", "speed", "cadence", "power", "altitude"]
    # Find which fields have data in at least 2 sources
    active_fields = []
    for field in stream_fields:
        count = sum(1 for s in sources if s["coverage"].get(_field_display(field), False))
        if count >= 2:
            active_fields.append(field)

    if not active_fields:
        return None

    result: dict = {"fields": active_fields, "sources": []}
    for src in sources:
        src_data: dict = {
            "label": f"{src['source_platform']}: {src['filename']}",
            "timestamps": [p["timestamp"] for p in src["track_points"]],
        }
        for field in active_fields:
            src_data[field] = [p.get(field) for p in src["track_points"]]
        result["sources"].append(src_data)
    return result


def _activity_prefix(activity_id: str) -> str:
    parts = activity_id.split("_", 2)
    return f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else activity_id


def _anomalies_path(cfg: Config) -> Path:
    return cfg.data_dir / "processed" / "anomalies.json"


def _load_anomalies(cfg: Config) -> list[dict]:
    path = _anomalies_path(cfg)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_anomalies(cfg: Config, anoms: list[dict]) -> None:
    path = _anomalies_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(anoms, indent=2))


def flag_activity(cfg: Config, activity_id: str) -> None:
    prefix = _activity_prefix(activity_id)
    anoms = _load_anomalies(cfg)
    # Don't duplicate
    if any(a["file"] == f"prefix:{prefix}" for a in anoms):
        return
    anoms.append({
        "file": f"prefix:{prefix}",
        "reasons": [{"reason": "Manually flagged via web UI", "severity": "warning"}],
    })
    _save_anomalies(cfg, anoms)
    invalidate_cache()


def unflag_activity(cfg: Config, activity_id: str) -> None:
    prefix = _activity_prefix(activity_id)
    anoms = _load_anomalies(cfg)
    anoms = [a for a in anoms if a["file"] != f"prefix:{prefix}"]
    _save_anomalies(cfg, anoms)
    invalidate_cache()


def delete_activity(cfg: Config, activity_id: str) -> None:
    # Remove processed files matching this ID
    for d in (cfg.processed_merged_dir(), cfg.processed_individual_dir()):
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.stem == activity_id:
                f.unlink()
    # Remove from anomalies if present
    prefix = _activity_prefix(activity_id)
    anoms = _load_anomalies(cfg)
    anoms = [a for a in anoms if a["file"] != f"prefix:{prefix}"]
    _save_anomalies(cfg, anoms)
    invalidate_cache()


def _field_display(field: str) -> str:
    return {
        "heart_rate": "Heart Rate",
        "speed": "Speed",
        "cadence": "Cadence",
        "power": "Power",
        "altitude": "Altitude",
        "temperature": "Temperature",
    }.get(field, field)


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

    sorted_activities = sorted(activities, key=lambda e: e.get("start_time") or "", reverse=True)
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

from dataclasses import dataclass
from datetime import datetime, timedelta

import typer

from fitgrabber.parsers.models import Activity, TrackPoint

POINT_MERGE_TOLERANCE = timedelta(seconds=2)
QUALITY_WINDOW_SECONDS = 60
QUALITY_DROPOUT_THRESHOLD = 0.20  # >20% dropout = unreliable

# Typical avg speed ranges (m/s) for sport inference
SPORT_SPEED_RANGES: list[tuple[str, float, float]] = [
    ("swimming", 0.3, 2.5),
    ("walking", 0.5, 2.2),
    ("hiking", 0.3, 2.0),
    ("running", 2.0, 6.5),
    ("cycling", 4.0, 25.0),
]

# Per-field platform priority (lower index = higher priority)
FIELD_PRIORITY: dict[str, list[str]] = {
    "gps": ["garmin", "coros", "suunto", "strava"],
    "heart_rate": ["garmin", "coros", "strava"],
    "power": ["stryd", "peloton", "garmin", "strava"],
    "cadence": ["garmin", "coros", "stryd", "strava"],
    "distance": ["garmin", "coros", "suunto", "strava"],
    "calories": ["garmin", "coros", "peloton", "strava"],
    "speed": ["garmin", "coros", "suunto", "strava"],
    "altitude": ["garmin", "coros", "suunto", "strava"],
    "temperature": ["garmin", "coros", "suunto", "strava"],
}

# Map track point fields to priority field groups
_POINT_FIELD_GROUP: dict[str, str] = {
    "latitude": "gps",
    "longitude": "gps",
    "altitude": "altitude",
    "heart_rate": "heart_rate",
    "cadence": "cadence",
    "speed": "speed",
    "power": "power",
    "temperature": "temperature",
    "distance": "distance",
}

_POINT_FIELDS = list(_POINT_FIELD_GROUP.keys())


@dataclass
class _TimeRange:
    start: datetime
    end: datetime

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts <= self.end


def _field_rank(platform: str, field_group: str, hr_source: str | None = None) -> int:
    """Return priority rank for a platform+field combo (lower = better)."""
    plist = FIELD_PRIORITY.get(field_group, [])
    # Chest strap HR gets boosted to top priority
    if field_group == "heart_rate" and hr_source == "chest":
        return -1  # Best possible
    # Wrist HR gets a small penalty (push down by 1)
    if field_group == "heart_rate" and hr_source == "wrist":
        try:
            return plist.index(platform) + 1
        except ValueError:
            return len(plist) + 1
    try:
        return plist.index(platform)
    except ValueError:
        return len(plist)


def _analyze_quality(
    points: list[TrackPoint], field: str, window_s: int = QUALITY_WINDOW_SECONDS
) -> list[_TimeRange]:
    """Find time ranges where a field has >20% dropout (zero/None values)."""
    if not points:
        return []

    unreliable: list[_TimeRange] = []
    window = timedelta(seconds=window_s)
    i = 0
    n = len(points)

    while i < n:
        win_start = points[i].timestamp
        win_end = win_start + window
        total = 0
        bad = 0
        j = i
        while j < n and points[j].timestamp <= win_end:
            total += 1
            val = getattr(points[j], field, None)
            if val is None or val == 0:
                bad += 1
            j += 1

        if total > 0 and bad / total > QUALITY_DROPOUT_THRESHOLD:
            unreliable.append(_TimeRange(start=win_start, end=win_end))

        # Slide by half-window
        next_i = i
        half = win_start + timedelta(seconds=window_s // 2)
        while next_i < n and points[next_i].timestamp < half:
            next_i += 1
        i = max(next_i, i + 1)

    return _merge_ranges(unreliable)


def _merge_ranges(ranges: list[_TimeRange]) -> list[_TimeRange]:
    """Merge overlapping time ranges."""
    if not ranges:
        return []
    ranges.sort(key=lambda r: r.start)
    merged = [ranges[0]]
    for r in ranges[1:]:
        if r.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, r.end)
        else:
            merged.append(r)
    return merged


def _is_unreliable(ts: datetime, ranges: list[_TimeRange]) -> bool:
    return any(r.contains(ts) for r in ranges)


def merge_activities(activities: list[Activity], verbose: bool = False) -> Activity:
    """Merge multiple recordings of the same activity into one."""
    if not activities:
        raise ValueError("No activities to merge")
    if len(activities) == 1:
        return activities[0]

    activities.sort(key=lambda a: len(a.track_points), reverse=True)

    # Build descriptive label for each source (platform, origin, time, points)
    def _source_label(a: Activity) -> str:
        platform = a.source_platform
        # For Strava, include original device/platform if known
        if platform == "strava" and a.metadata:
            origin = a.metadata.get("original_platform")
            if origin:
                platform = f"strava({origin})"
        time_str = a.start_time.strftime("%Y-%m-%d %H:%M") if a.start_time else "?"
        return f"{platform} {time_str} ({len(a.track_points)} pts)"

    sources_label = ", ".join(_source_label(a) for a in activities)
    typer.echo(f"  Merging {len(activities)} sources: {sources_label}")

    sport = _resolve_sport(activities, verbose)

    # Build per-source, per-field quality maps
    quality_issues: dict[str, dict[str, list[_TimeRange]]] = {}  # platform -> field -> ranges
    for a in activities:
        issues: dict[str, list[_TimeRange]] = {}
        for field in _POINT_FIELDS:
            if field in ("latitude", "longitude"):
                continue  # GPS quality checked together below
            ranges = _analyze_quality(a.track_points, field)
            if ranges:
                issues[field] = ranges
        # GPS: check latitude as proxy
        gps_ranges = _analyze_quality(a.track_points, "latitude")
        if gps_ranges:
            issues["latitude"] = gps_ranges
            issues["longitude"] = gps_ranges
        if issues:
            quality_issues[a.source_platform] = issues
            for field, ranges in issues.items():
                for r in ranges:
                    typer.echo(
                        f"    Data quality: {a.source_platform} {field} unreliable "
                        f"{r.start.strftime('%H:%M:%S')}-{r.end.strftime('%H:%M:%S')}"
                    )

    # Collect all points tagged with source
    tagged: list[tuple[TrackPoint, Activity]] = []
    for a in activities:
        for pt in a.track_points:
            tagged.append((pt, a))
    tagged.sort(key=lambda t: t[0].timestamp)

    # Group by time window and merge per-field with priority
    merged: list[TrackPoint] = []
    secondary_fills: dict[str, dict[str, int]] = {}  # field -> {platform: count}
    i = 0
    n = len(tagged)
    while i < n:
        group = [tagged[i]]
        j = i + 1
        while j < n and tagged[j][0].timestamp - group[0][0].timestamp <= POINT_MERGE_TOLERANCE:
            group.append(tagged[j])
            j += 1
        merged.append(_merge_points_priority(group, quality_issues, secondary_fills))
        i = j

    laps, lap_source = _select_laps(activities, verbose)

    # Summary fields from highest-priority source
    result_distance = _priority_summary(activities, "total_distance", "distance")
    result_duration = _priority_summary(activities, "total_duration", "distance")
    result_calories = _priority_summary(activities, "total_calories", "calories")
    max_hr = max((a.max_heart_rate or 0) for a in activities) or None

    # Recalculate averages from merged track points
    avg_hr, hr_count, hr_excluded = _calc_avg_int(merged, "heart_rate")
    avg_speed, _, _ = _calc_avg_float(merged, "speed")
    avg_cadence, _, _ = _calc_avg_int(merged, "cadence")
    avg_power, _, _ = _calc_avg_int(merged, "power")

    power_source, power_source_alt = _resolve_merged_power_source(activities)
    hr_source = _best_hr_source(activities)
    hr_detail, rr_intervals = _select_rr(activities)

    # Merge metadata and notes
    meta = {
        "merged_from": [str(a.source_file) for a in activities],
        "sources": [
            {
                "platform": a.source_platform,
                "power_source": a.power_source,
                "hr_source": a.hr_source,
            }
            for a in activities
        ],
    }
    for a in activities:
        for k, v in a.metadata.items():
            if k not in meta:
                meta[k] = v
    notes_parts = []
    for a in activities:
        if a.notes:
            notes_parts.append(f"[{a.source_platform}] {a.notes}")
    name = next((a.name for a in activities if a.name), "")

    # Logging
    if verbose or True:  # Always log key merge info
        _log_merge_summary(
            activities,
            sport,
            result_distance,
            result_duration,
            result_calories,
            avg_hr,
            hr_count,
            hr_excluded,
            max_hr,
            merged,
            secondary_fills,
            lap_source,
            laps,
        )
        if power_source:
            alt = f" (alt: {power_source_alt})" if power_source_alt else ""
            typer.echo(f"    Power source: {power_source}{alt}")
        if hr_detail == "rr":
            typer.echo(f"    HR detail: R-R intervals ({len(rr_intervals)} beats)")

    return Activity(
        source_file=activities[0].source_file,
        source_platform="merged",
        sport=sport,
        start_time=merged[0].timestamp if merged else activities[0].start_time,
        end_time=merged[-1].timestamp if merged else activities[0].end_time,
        track_points=merged,
        laps=laps,
        total_distance=result_distance,
        total_duration=result_duration,
        total_calories=result_calories,
        avg_heart_rate=avg_hr,
        max_heart_rate=max_hr,
        avg_speed=avg_speed,
        avg_cadence=avg_cadence,
        avg_power=avg_power,
        hr_source=hr_source,
        hr_detail=hr_detail,
        power_source=power_source,
        power_source_alt=power_source_alt,
        rr_intervals=rr_intervals,
        name=name,
        notes="\n".join(notes_parts),
        metadata=meta,
    )


def _resolve_merged_power_source(activities: list[Activity]) -> tuple[str | None, str | None]:
    """Canonical power source for the merged activity (Stryd wins if any source has it)."""
    srcs = [a.power_source for a in activities if a.power_source]
    if not srcs:
        return None, None
    native_present = any(
        a.power_source == "garmin_native" or (a.power_source_alt and "native" in a.power_source_alt)
        for a in activities
    )
    if "stryd" in srcs:
        return "stryd", ("garmin_native" if native_present else None)
    for pref in ("garmin_native", "strava"):
        if pref in srcs:
            return pref, None
    return srcs[0], None


def _best_hr_source(activities: list[Activity]) -> str | None:
    sources = {a.hr_source for a in activities if a.hr_source}
    if "chest" in sources:
        return "chest"
    if "wrist" in sources:
        return "wrist"
    return None


def _select_rr(activities: list[Activity]) -> tuple[str | None, list[float]]:
    """Keep beat-to-beat R-R from the first source that has it (the chest strap)."""
    for a in activities:
        if a.rr_intervals:
            return "rr", a.rr_intervals
    return None, []


def _priority_summary(
    activities: list[Activity], attr: str, field_group: str
) -> float | int | None:
    """Get summary field value from highest-priority source that has it."""
    ranked = sorted(
        activities,
        key=lambda a: _field_rank(a.source_platform, field_group, a.hr_source),
    )
    for a in ranked:
        val = getattr(a, attr, None)
        if val is not None:
            return val
    return None


def _merge_points_priority(
    group: list[tuple[TrackPoint, Activity]],
    quality_issues: dict[str, dict[str, list[_TimeRange]]],
    secondary_fills: dict[str, dict[str, int]],
) -> TrackPoint:
    """Merge a group of near-simultaneous points using per-field priority."""
    ts = group[0][0].timestamp
    result = TrackPoint(timestamp=ts)

    for field in _POINT_FIELDS:
        field_group = _POINT_FIELD_GROUP[field]
        # Sort sources by priority for this field
        candidates = sorted(
            group,
            key=lambda g: _field_rank(g[1].source_platform, field_group, g[1].hr_source),
        )

        best_val = None
        best_platform = None
        for pt, act in candidates:
            platform = act.source_platform
            # Skip if this source is unreliable for this field at this time
            src_issues = quality_issues.get(platform, {}).get(field, [])
            if src_issues and _is_unreliable(ts, src_issues):
                continue
            val = getattr(pt, field, None)
            if val is not None and val != 0:
                best_val = val
                best_platform = platform
                break

        if best_val is not None:
            setattr(result, field, best_val)
            # Track secondary fills (not from top-priority source)
            if best_platform and len(candidates) > 1:
                top_platform = candidates[0][1].source_platform
                if best_platform != top_platform:
                    fills = secondary_fills.setdefault(field, {})
                    fills[best_platform] = fills.get(best_platform, 0) + 1

    return result


def _calc_avg_int(points: list[TrackPoint], field: str) -> tuple[int | None, int, int]:
    """Calculate average of an int field, excluding zero/None dropout stretches."""
    total = 0
    count = 0
    excluded = 0
    for pt in points:
        val = getattr(pt, field, None)
        if val is not None and val > 0:
            total += val
            count += 1
        else:
            excluded += 1
    return (round(total / count) if count else None), count, excluded


def _calc_avg_float(points: list[TrackPoint], field: str) -> tuple[float | None, int, int]:
    total = 0.0
    count = 0
    excluded = 0
    for pt in points:
        val = getattr(pt, field, None)
        if val is not None and val > 0:
            total += val
            count += 1
        else:
            excluded += 1
    return (total / count if count else None), count, excluded


def fill_missing_summary(activity: Activity) -> None:
    """Populate missing summary scalars from track points, in place.

    Only fills fields the source left empty (e.g. Strava, which carries HR/speed
    streams but no summary scalars). Calories is not derivable from streams.
    """
    pts = activity.track_points
    if not pts:
        return
    if activity.avg_heart_rate is None:
        activity.avg_heart_rate, _, _ = _calc_avg_int(pts, "heart_rate")
    if activity.max_heart_rate is None:
        hrs = [p.heart_rate for p in pts if p.heart_rate and p.heart_rate > 0]
        activity.max_heart_rate = max(hrs) if hrs else None
    if activity.avg_speed is None:
        activity.avg_speed, _, _ = _calc_avg_float(pts, "speed")
    if activity.avg_cadence is None:
        activity.avg_cadence, _, _ = _calc_avg_int(pts, "cadence")
    if activity.avg_power is None:
        activity.avg_power, _, _ = _calc_avg_int(pts, "power")


def _log_merge_summary(
    activities: list[Activity],
    sport: str,
    distance: float | int | None,
    duration: float | int | None,
    calories: float | int | None,
    avg_hr: int | None,
    hr_count: int,
    hr_excluded: int,
    max_hr: int | None,
    merged: list[TrackPoint],
    secondary_fills: dict[str, dict[str, int]],
    lap_source: str,
    laps: list,
) -> None:
    sports = set(a.sport for a in activities)
    if len(sports) == 1:
        typer.echo(f"    Sport: {sport} (all agree)")
    else:
        labels = ", ".join(f"{a.source_platform}={a.sport}" for a in activities)
        typer.echo(f"    Sport: {sport} (resolved from: {labels})")

    if distance is not None:
        vals = ", ".join(
            f"{a.source_platform}={a.total_distance}" for a in activities if a.total_distance
        )
        typer.echo(f"    Distance: {distance:.0f}m ({vals})")
        # Warn on >5% difference
        dists = [a.total_distance for a in activities if a.total_distance]
        if len(dists) > 1 and max(dists) > 0:
            diff_pct = (max(dists) - min(dists)) / max(dists) * 100
            if diff_pct > 5:
                typer.echo(f"    ⚠ Distance difference: {diff_pct:.1f}%")

    if duration is not None:
        typer.echo(f"    Duration: {duration:.0f}s")
        durs = [a.total_duration for a in activities if a.total_duration]
        if len(durs) > 1 and max(durs) > 0:
            diff_pct = (max(durs) - min(durs)) / max(durs) * 100
            if diff_pct > 5:
                typer.echo(f"    ⚠ Duration difference: {diff_pct:.1f}%")

    if calories is not None:
        typer.echo(f"    Calories: {calories}")

    hr_labels = []
    for a in activities:
        src = f" ({a.hr_source})" if a.hr_source else ""
        hr_labels.append(f"{a.source_platform}{src}")
    typer.echo(f"    HR sources: {', '.join(hr_labels)}")

    total_raw = sum(len(a.track_points) for a in activities)
    typer.echo(f"    Track points: {len(merged)} merged from {total_raw} raw")

    if secondary_fills:
        parts = []
        for field, platforms in secondary_fills.items():
            for plat, cnt in platforms.items():
                parts.append(f"{field} ({plat}: {cnt} pts)")
        typer.echo(f"    Fields filled from secondary: {', '.join(parts)}")

    if avg_hr is not None:
        typer.echo(f"    Avg HR: {avg_hr} (from {hr_count} pts, excluded {hr_excluded} dropout)")
    if max_hr is not None:
        vals = ", ".join(
            f"{a.source_platform}={a.max_heart_rate}" for a in activities if a.max_heart_rate
        )
        typer.echo(f"    Max HR: {max_hr} (max across: {vals})")

    if laps:
        typer.echo(f"    Laps: {lap_source} ({len(laps)} laps)")
        for a in activities:
            if a.source_platform != lap_source and a.laps:
                score = _lap_score(a)
                typer.echo(
                    f"    Laps discarded: {a.source_platform} ({len(a.laps)} laps, score={score})"
                )


def _normalize_sport(sport: str) -> str:
    from fitgrabber.processing.sports import sport_category

    return sport_category(sport)


def _resolve_sport(activities: list[Activity], verbose: bool) -> str:
    """Pick the best sport type, inferring from data if sources disagree."""
    normalized = [
        _normalize_sport(a.sport)
        for a in activities
        if a.sport and a.sport not in ("unknown", "other")
    ]
    unique = set(normalized)

    if len(unique) == 1:
        return unique.pop()

    if not unique:
        inferred = _infer_sport(activities)
        if verbose:
            typer.echo(f"    Sport unknown across sources → inferred '{inferred}'")
        return inferred

    counts: dict[str, int] = {}
    for s in normalized:
        counts[s] = counts.get(s, 0) + 1
    top_count = max(counts.values())
    winners = [s for s, c in counts.items() if c == top_count]

    if len(winners) == 1:
        winner = winners[0]
    else:
        specific = [s for s in winners if s not in ("other", "strength")]
        if len(specific) == 1:
            winner = specific[0]
        else:
            winner = _infer_sport(activities)

    labels = ", ".join(f"{a.source_platform}={a.sport}" for a in activities)
    typer.echo(f"    Sport mismatch: {labels} → using '{winner}'")
    return winner


def _infer_sport(activities: list[Activity]) -> str:
    """Infer sport type from speed, cadence, and power data."""
    speeds: list[float] = []
    for a in activities:
        if a.avg_speed:
            speeds.append(a.avg_speed)
        elif a.total_distance and a.total_duration and a.total_duration > 0:
            speeds.append(a.total_distance / a.total_duration)

    has_power = any(a.avg_power for a in activities)
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    if has_power and avg_speed:
        if avg_speed > 4.0:
            return "cycling"
        return "running"

    if avg_speed is not None:
        best = "unknown"
        best_fit = float("inf")
        for sport, lo, hi in SPORT_SPEED_RANGES:
            mid = (lo + hi) / 2
            dist = abs(avg_speed - mid)
            if lo <= avg_speed <= hi and dist < best_fit:
                best = sport
                best_fit = dist
        if best != "unknown":
            return best
        if avg_speed < 0.3:
            return "swimming"
        if avg_speed > 6.5:
            return "cycling"

    sport_counts: dict[str, int] = {}
    for a in activities:
        s = a.sport.lower() if a.sport else "unknown"
        if s != "unknown":
            sport_counts[s] = sport_counts.get(s, 0) + 1
    if sport_counts:
        return max(sport_counts, key=sport_counts.get)  # type: ignore[arg-type]
    return "unknown"


_LAP_TRIGGER_PRIORITY = {"manual": 3, "interval": 2, "distance": 1, "session_end": 0}


def _lap_score(activity: Activity) -> int:
    if not activity.laps:
        return -1
    triggers = {lap.lap_trigger for lap in activity.laps if lap.lap_trigger}
    best = max((_LAP_TRIGGER_PRIORITY.get(t, 0) for t in triggers), default=0)
    return best + (1 if len(activity.laps) > 1 else 0)


def _select_laps(activities: list[Activity], verbose: bool) -> tuple[list, str]:
    best_a = max(activities, key=_lap_score)
    if not best_a.laps:
        return [], ""
    if verbose:
        typer.echo(f"    Laps: using {best_a.source_platform} ({len(best_a.laps)} laps)")
    return best_a.laps, best_a.source_platform

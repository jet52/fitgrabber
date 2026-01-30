from datetime import timedelta

import typer

from fitgrabber.parsers.models import Activity, TrackPoint

POINT_MERGE_TOLERANCE = timedelta(seconds=2)

# Typical avg speed ranges (m/s) for sport inference
SPORT_SPEED_RANGES: list[tuple[str, float, float]] = [
    ("swimming", 0.3, 2.5),
    ("walking", 0.5, 2.2),
    ("hiking", 0.3, 2.0),
    ("running", 2.0, 6.5),
    ("cycling", 4.0, 25.0),
]


def merge_activities(activities: list[Activity], verbose: bool = False) -> Activity:
    """Merge multiple recordings of the same activity into one.

    Takes the union of time ranges and data fields from each source.
    If sport types disagree, infers the best type from the data.
    """
    if not activities:
        raise ValueError("No activities to merge")
    if len(activities) == 1:
        return activities[0]

    # Use the activity with the most track points as the base
    activities.sort(key=lambda a: len(a.track_points), reverse=True)
    base = activities[0]

    # Resolve sport type
    sport = _resolve_sport(activities, verbose)

    # Collect all points from all sources, sorted by time
    all_points: list[TrackPoint] = []
    for a in activities:
        all_points.extend(a.track_points)
    all_points.sort(key=lambda p: p.timestamp)

    # Merge points that are close in time
    merged: list[TrackPoint] = []
    i = 0
    while i < len(all_points):
        group = [all_points[i]]
        j = i + 1
        while (
            j < len(all_points)
            and all_points[j].timestamp - group[0].timestamp <= POINT_MERGE_TOLERANCE
        ):
            group.append(all_points[j])
            j += 1
        merged.append(_merge_points(group))
        i = j

    return Activity(
        source_file=base.source_file,
        source_platform="merged",
        sport=sport,
        start_time=merged[0].timestamp if merged else base.start_time,
        end_time=merged[-1].timestamp if merged else base.end_time,
        track_points=merged,
        total_distance=max((a.total_distance or 0) for a in activities) or None,
        total_duration=max((a.total_duration or 0) for a in activities) or None,
        total_calories=max((a.total_calories or 0) for a in activities) or None,
        avg_heart_rate=base.avg_heart_rate,
        max_heart_rate=max((a.max_heart_rate or 0) for a in activities) or None,
        name=base.name or next((a.name for a in activities if a.name), ""),
        metadata={"merged_from": [str(a.source_file) for a in activities]},
    )


SPORT_ALIASES: dict[str, str] = {
    "run": "running",
    "trailrun": "running",
    "trail_run": "running",
    "virtualrun": "running",
    "ride": "cycling",
    "virtualride": "cycling",
    "mountainbikeride": "cycling",
    "ebikeride": "cycling",
    "gravelride": "cycling",
    "swim": "swimming",
    "walk": "walking",
    "hike": "hiking",
    "weighttraining": "strength",
    "training": "strength",
    "workout": "strength",
    "generic": "other",
    "backcountryski": "skiing",
    "nordicski": "skiing",
    "alpineski": "skiing",
}


def _normalize_sport(sport: str) -> str:
    return SPORT_ALIASES.get(sport.lower(), sport.lower())


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

    # Majority vote among normalized sports
    counts: dict[str, int] = {}
    for s in normalized:
        counts[s] = counts.get(s, 0) + 1
    top_count = max(counts.values())
    winners = [s for s, c in counts.items() if c == top_count]

    if len(winners) == 1:
        winner = winners[0]
    else:
        # Tie — prefer specific sports over vague categories
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
    # Gather average speed from metadata or compute from track points
    speeds: list[float] = []
    for a in activities:
        if a.avg_speed:
            speeds.append(a.avg_speed)
        elif a.total_distance and a.total_duration and a.total_duration > 0:
            speeds.append(a.total_distance / a.total_duration)

    has_power = any(a.avg_power for a in activities)
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    # Power sensor is a strong signal for cycling or running
    if has_power and avg_speed:
        if avg_speed > 4.0:
            return "cycling"
        return "running"

    if avg_speed is not None:
        # Find best matching sport by speed
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

        # Out of all ranges — pick closest
        if avg_speed < 0.3:
            return "swimming"
        if avg_speed > 6.5:
            return "cycling"

    # Fall back to most common non-unknown sport
    sport_counts: dict[str, int] = {}
    for a in activities:
        s = a.sport.lower() if a.sport else "unknown"
        if s != "unknown":
            sport_counts[s] = sport_counts.get(s, 0) + 1
    if sport_counts:
        return max(sport_counts, key=sport_counts.get)  # type: ignore[arg-type]
    return "unknown"


def _merge_points(points: list[TrackPoint]) -> TrackPoint:
    """Merge a group of near-simultaneous points, preferring non-None values."""
    base = points[0]
    for pt in points[1:]:
        base.latitude = base.latitude or pt.latitude
        base.longitude = base.longitude or pt.longitude
        base.altitude = base.altitude or pt.altitude
        base.heart_rate = base.heart_rate or pt.heart_rate
        base.cadence = base.cadence or pt.cadence
        base.speed = base.speed or pt.speed
        base.power = base.power or pt.power
        base.temperature = base.temperature or pt.temperature
        base.distance = base.distance or pt.distance
    return base

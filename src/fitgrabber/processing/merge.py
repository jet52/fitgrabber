from datetime import timedelta

from fitgrabber.parsers.models import Activity, TrackPoint

POINT_MERGE_TOLERANCE = timedelta(seconds=2)


def merge_activities(activities: list[Activity]) -> Activity:
    """Merge multiple recordings of the same activity into one.

    Takes the union of time ranges and data fields from each source.
    """
    if not activities:
        raise ValueError("No activities to merge")
    if len(activities) == 1:
        return activities[0]

    # Use the activity with the most track points as the base
    activities.sort(key=lambda a: len(a.track_points), reverse=True)
    base = activities[0]

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
        sport=base.sport,
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

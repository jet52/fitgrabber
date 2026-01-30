from pathlib import Path

import gpxpy

from fitgrabber.parsers.models import Activity, TrackPoint


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    with open(filepath) as f:
        gpx = gpxpy.parse(f)

    activity = Activity(
        source_file=filepath,
        source_platform=platform,
        name=gpx.name or "",
        sport=gpx.type or "unknown",
    )

    points: list[TrackPoint] = []
    for track in gpx.tracks:
        if track.type and activity.sport == "unknown":
            activity.sport = track.type
        for segment in track.segments:
            for pt in segment.points:
                if pt.time is None:
                    continue
                points.append(
                    TrackPoint(
                        timestamp=pt.time,
                        latitude=pt.latitude,
                        longitude=pt.longitude,
                        altitude=pt.elevation,
                        heart_rate=_get_extension(pt, "hr"),
                        cadence=_get_extension(pt, "cad"),
                        power=_get_extension(pt, "power"),
                        speed=pt.speed_between(segment.points[segment.points.index(pt) - 1])
                        if segment.points.index(pt) > 0
                        else None,
                    )
                )

    activity.track_points = points
    if points:
        activity.start_time = points[0].timestamp
        activity.end_time = points[-1].timestamp

    bounds = gpx.get_moving_data()
    if bounds:
        activity.total_distance = bounds.moving_distance + bounds.stopped_distance
        activity.total_duration = bounds.moving_time + bounds.stopped_time

    return activity


def _get_extension(point, name: str) -> int | None:
    for ext in point.extensions:
        for child in ext:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag.lower() == name.lower():
                try:
                    return int(child.text)
                except (ValueError, TypeError):
                    pass
    return None

from pathlib import Path

import fitdecode

from fitgrabber.parsers.models import Activity, TrackPoint


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    activity = Activity(source_file=filepath, source_platform=platform)
    points: list[TrackPoint] = []

    with fitdecode.FitReader(str(filepath)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue

            if frame.name == "session":
                activity.sport = _get_field(frame, "sport", "unknown")
                activity.start_time = _get_field(frame, "start_time")
                activity.total_distance = _get_field(frame, "total_distance")
                activity.total_duration = _get_field(frame, "total_elapsed_time")
                activity.total_calories = _get_field(frame, "total_calories")
                activity.avg_heart_rate = _get_field(frame, "avg_heart_rate")
                activity.max_heart_rate = _get_field(frame, "max_heart_rate")
                activity.avg_speed = _get_field(frame, "avg_speed")
                activity.avg_cadence = _get_field(frame, "avg_cadence")
                activity.avg_power = _get_field(frame, "avg_power")

            elif frame.name == "record":
                ts = _get_field(frame, "timestamp")
                if ts is None:
                    continue
                pt = TrackPoint(
                    timestamp=ts,
                    latitude=_semicircles_to_deg(_get_field(frame, "position_lat")),
                    longitude=_semicircles_to_deg(_get_field(frame, "position_long")),
                    altitude=_get_field(frame, "altitude"),
                    heart_rate=_get_field(frame, "heart_rate"),
                    cadence=_get_field(frame, "cadence"),
                    speed=_get_field(frame, "speed"),
                    power=_get_field(frame, "power"),
                    temperature=_get_field(frame, "temperature"),
                    distance=_get_field(frame, "distance"),
                )
                points.append(pt)

    activity.track_points = points
    if points:
        activity.start_time = activity.start_time or points[0].timestamp
        activity.end_time = points[-1].timestamp
    return activity


def _get_field(frame: fitdecode.FitDataMessage, name: str, default=None):
    try:
        f = frame.get_field(name)
        return f.value if f else default
    except (KeyError, AttributeError):
        return default


def _semicircles_to_deg(val: int | float | None) -> float | None:
    if val is None:
        return None
    return val * (180.0 / 2**31)

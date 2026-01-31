from pathlib import Path

import fitdecode

from fitgrabber.parsers.models import Activity, Lap, TrackPoint


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    activity = Activity(source_file=filepath, source_platform=platform)
    points: list[TrackPoint] = []
    laps: list[Lap] = []

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

            elif frame.name == "lap":
                lap = _parse_lap(frame)
                if lap:
                    laps.append(lap)

    activity.track_points = points
    activity.laps = laps
    if points:
        activity.start_time = activity.start_time or points[0].timestamp
        activity.end_time = points[-1].timestamp
    return activity


_RUNNING_DYNAMICS_FIELDS = (
    "stance_time",
    "stance_time_percent",
    "vertical_oscillation",
    "vertical_ratio",
    "step_length",
    "ground_contact_time_balance",
)


def _parse_lap(frame: fitdecode.FitDataMessage) -> Lap | None:
    start = _get_field(frame, "start_time")
    ts = _get_field(frame, "timestamp")
    if not start or not ts:
        return None

    extra: dict = {}
    for f in _RUNNING_DYNAMICS_FIELDS:
        val = _get_field(frame, f)
        if val is not None:
            extra[f] = val
    # Capture developer fields
    for f in frame.fields:
        if hasattr(f, "is_dev") and f.is_dev and f.value is not None:
            extra[f.name] = f.value

    trigger = _get_field(frame, "lap_trigger")
    intensity = _get_field(frame, "intensity")

    return Lap(
        start_time=start,
        end_time=ts,
        total_distance=_get_field(frame, "total_distance"),
        total_duration=_get_field(frame, "total_elapsed_time"),
        total_calories=_get_field(frame, "total_calories"),
        avg_heart_rate=_get_field(frame, "avg_heart_rate"),
        max_heart_rate=_get_field(frame, "max_heart_rate"),
        avg_speed=_get_field(frame, "avg_speed"),
        avg_cadence=_get_field(frame, "avg_cadence"),
        avg_power=_get_field(frame, "avg_power"),
        max_power=_get_field(frame, "max_power"),
        lap_trigger=str(trigger) if trigger is not None else None,
        intensity=str(intensity) if intensity is not None else None,
        sport=_get_field(frame, "sport"),
        extra=extra,
    )


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

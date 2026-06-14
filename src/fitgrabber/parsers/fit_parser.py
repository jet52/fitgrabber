from datetime import timedelta
from pathlib import Path

import fitdecode

from fitgrabber.parsers.models import Activity, Lap, TrackPoint


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    activity = Activity(source_file=filepath, source_platform=platform)
    points: list[TrackPoint] = []
    laps: list[Lap] = []
    devices: list[dict] = []
    rr: list[float] = []
    saw_stryd_power = False
    saw_native_power = False

    with fitdecode.FitReader(str(filepath)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue

            if frame.name == "device_info":
                devices.append(_device_summary(frame))

            elif frame.name == "hrv":
                rr.extend(_get_rr_intervals(frame))

            elif frame.name == "session":
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
                native_power = _get_field(frame, "power")
                stryd_power = _get_dev_power(frame)
                if stryd_power is not None:
                    saw_stryd_power = True
                if native_power is not None:
                    saw_native_power = True
                pt = TrackPoint(
                    timestamp=ts,
                    latitude=_semicircles_to_deg(_get_field(frame, "position_lat")),
                    longitude=_semicircles_to_deg(_get_field(frame, "position_long")),
                    altitude=_get_field(frame, "altitude"),
                    heart_rate=_get_field(frame, "heart_rate"),
                    cadence=_get_field(frame, "cadence"),
                    speed=_get_field(frame, "speed"),
                    power=stryd_power if stryd_power is not None else native_power,
                    power_native=native_power,
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
    activity.hr_source = _detect_hr_source(devices)
    if rr:
        activity.rr_intervals = rr
        activity.hr_detail = "rr"
        # Beat-to-beat data only comes from a strap; trust it over ambiguous device_info.
        if activity.hr_source is None:
            activity.hr_source = "chest"
    activity.power_source, activity.power_source_alt = _resolve_power_source(
        platform, saw_stryd_power, saw_native_power
    )
    if points:
        activity.start_time = activity.start_time or points[0].timestamp
        activity.end_time = points[-1].timestamp
    return activity


def _resolve_power_source(
    platform: str, saw_stryd: bool, saw_native: bool
) -> tuple[str | None, str | None]:
    """Determine canonical power source and a secondary label.

    A Stryd developer "Power" field (embedded by the Stryd Connect IQ app) is
    canonical when present; device-native power is kept as the alternate. With no
    Stryd field, the native power is labeled by platform.
    """
    native_label = "garmin_native" if platform == "garmin" else f"{platform}_native"
    if saw_stryd:
        return "stryd", (native_label if saw_native else None)
    if saw_native:
        if platform == "stryd":
            return "stryd", None
        return native_label, None
    return None, None


def parse_summary(filepath: Path, platform: str = "unknown") -> Activity:
    """Parse only session-level metadata from a FIT file, skipping track points."""
    activity = Activity(source_file=filepath, source_platform=platform)
    num_points = 0

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
                activity.end_time = _get_field(frame, "timestamp")
            elif frame.name == "record":
                num_points += 1

    activity.track_points = [None] * num_points  # type: ignore[list-item]
    return activity


def _device_summary(frame: fitdecode.FitDataMessage) -> dict:
    return {
        "device_type": _get_field(frame, "device_type"),
        "ant_device_type": _get_field(frame, "ant_device_type"),
        "source_type": _get_field(frame, "source_type"),
    }


def _detect_hr_source(devices: list[dict]) -> str | None:
    """Classify the HR sensor across all device_info messages.

    An external strap (ANT+/BLE HR monitor, device_type 120 / "heart_rate")
    wins over the built-in wrist optical sensor ("whr") when both are present.
    """
    saw_chest = False
    saw_wrist = False
    for d in devices:
        dt = str(d["device_type"]) if d["device_type"] is not None else None
        ant = str(d["ant_device_type"]) if d["ant_device_type"] is not None else None
        src = str(d["source_type"]) if d["source_type"] is not None else None

        is_hr = dt in ("120", "heart_rate") or ant == "120"
        if dt == "whr":
            saw_wrist = True
        elif is_hr:
            # External strap (ANT+ or BLE); "local" HR here would be the optical.
            if src in ("antplus", "bluetooth_low_energy") or src not in ("local", None):
                saw_chest = True
            else:
                saw_wrist = True

    if saw_chest:
        return "chest"
    if saw_wrist:
        return "wrist"
    return None


def _get_dev_power(frame: fitdecode.FitDataMessage) -> int | None:
    """Read the Stryd developer "Power" field from a record, if present.

    The native power field is lowercase "power"; the Stryd Connect IQ app writes
    a capitalized "Power" developer field, so the name is unambiguous.
    """
    for f in frame.fields:
        if f.name == "Power" and f.value is not None:
            return int(f.value)
    return None


def _get_rr_intervals(frame: fitdecode.FitDataMessage) -> list[float]:
    """Extract valid R-R intervals (seconds) from an hrv message's time array."""
    val = _get_field(frame, "time")
    if val is None:
        return []
    if not isinstance(val, (list, tuple)):
        val = [val]
    return [float(v) for v in val if v is not None and v > 0]


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

    # Some Garmin laps write a constant (activity-start) into the lap `timestamp`
    # field instead of the lap end, leaving a degenerate window. Derive the end
    # from start + elapsed time, which is authoritative, when it is the later value.
    elapsed = _get_field(frame, "total_elapsed_time")
    end = ts
    if elapsed:
        derived_end = start + timedelta(seconds=elapsed)
        if derived_end > end:
            end = derived_end

    return Lap(
        start_time=start,
        end_time=end,
        total_distance=_get_field(frame, "total_distance"),
        total_duration=elapsed,
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

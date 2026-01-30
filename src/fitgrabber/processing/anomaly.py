from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt

from fitgrabber.parsers.models import Activity, TrackPoint

# Max plausible running speed: ~6.5 m/s (~4:15/km pace)
# Max plausible cycling speed: ~22 m/s (~80 km/h)
# Anything above these thresholds for sustained periods is suspicious
SPEED_THRESHOLDS = {
    "running": 7.0,  # m/s
    "cycling": 25.0,
    "swimming": 3.0,
    "default": 15.0,
}

# GPS jump threshold: if two consecutive points are impossibly far apart
MAX_GPS_JUMP_M = 500  # meters in one second


@dataclass
class Anomaly:
    index: int
    point: TrackPoint
    reason: str
    severity: str  # "warning" or "error"


def detect_anomalies(activity: Activity) -> list[Anomaly]:
    """Find spurious data points in an activity."""
    anomalies: list[Anomaly] = []
    points = activity.track_points
    if len(points) < 2:
        return anomalies

    speed_limit = SPEED_THRESHOLDS.get(activity.sport.lower(), SPEED_THRESHOLDS["default"])

    for i in range(1, len(points)):
        prev, curr = points[i - 1], points[i]
        dt = (curr.timestamp - prev.timestamp).total_seconds()
        if dt <= 0:
            continue

        # Check GPS jump
        if prev.latitude and prev.longitude and curr.latitude and curr.longitude:
            dist = _haversine(prev.latitude, prev.longitude, curr.latitude, curr.longitude)
            speed = dist / dt

            if dist > MAX_GPS_JUMP_M and dt < 5:
                anomalies.append(Anomaly(i, curr, f"GPS jump: {dist:.0f}m in {dt:.0f}s", "error"))
            elif speed > speed_limit:
                anomalies.append(
                    Anomaly(
                        i,
                        curr,
                        f"Speed {speed:.1f} m/s exceeds {speed_limit} for {activity.sport}",
                        "warning",
                    )
                )

        # Check recorded speed field
        if curr.speed and curr.speed > speed_limit * 1.5:
            anomalies.append(
                Anomaly(
                    i,
                    curr,
                    f"Recorded speed {curr.speed:.1f} m/s exceeds threshold",
                    "warning",
                )
            )

    return anomalies


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS coordinates."""
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

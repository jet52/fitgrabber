from pathlib import Path

from tcxreader.tcxreader import TCXReader

from fitgrabber.parsers.models import Activity, TrackPoint


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    tcx = TCXReader()
    data = tcx.read(str(filepath))

    activity = Activity(
        source_file=filepath,
        source_platform=platform,
        sport=getattr(data, "activity_type", "unknown") or "unknown",
        start_time=data.start_time if hasattr(data, "start_time") else None,
        total_distance=data.distance if hasattr(data, "distance") else None,
        total_duration=data.duration if hasattr(data, "duration") else None,
        total_calories=int(data.calories) if hasattr(data, "calories") and data.calories else None,
        avg_heart_rate=int(data.hr_avg) if hasattr(data, "hr_avg") and data.hr_avg else None,
        max_heart_rate=int(data.hr_max) if hasattr(data, "hr_max") and data.hr_max else None,
        avg_cadence=int(data.cadence_avg)
        if hasattr(data, "cadence_avg") and data.cadence_avg
        else None,
    )

    points: list[TrackPoint] = []
    if hasattr(data, "trackpoints"):
        for tp in data.trackpoints:
            if tp.time is None:
                continue
            points.append(
                TrackPoint(
                    timestamp=tp.time,
                    latitude=tp.latitude,
                    longitude=tp.longitude,
                    altitude=tp.elevation,
                    heart_rate=int(tp.hr_value) if tp.hr_value else None,
                    cadence=int(tp.cadence) if hasattr(tp, "cadence") and tp.cadence else None,
                    distance=tp.distance if hasattr(tp, "distance") else None,
                )
            )

    activity.track_points = points
    if points:
        activity.start_time = activity.start_time or points[0].timestamp
        activity.end_time = points[-1].timestamp
    elif activity.start_time is None:
        # Summary-only TCX (no <Trackpoint> elements): tcxreader can't derive a
        # start time. Recover it (and duration) from the <Id>/<Lap> summary.
        start, duration = _summary_from_xml(filepath)
        if start:
            activity.start_time = start
            if duration and not activity.total_duration:
                activity.total_duration = duration
    return activity


def _summary_from_xml(filepath: Path) -> tuple[object | None, float | None]:
    """Read activity start time and total duration from a TCX without trackpoints."""
    import xml.etree.ElementTree as ET
    from datetime import datetime

    def _iso(text: str) -> object | None:
        try:
            return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    try:
        root = ET.parse(str(filepath)).getroot()
    except (ET.ParseError, OSError):
        return None, None

    start = None
    duration = 0.0
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip namespace
        if start is None and tag == "Id" and el.text:
            start = _iso(el.text)
        elif tag == "Lap":
            if start is None and el.get("StartTime"):
                start = _iso(el.get("StartTime"))
        elif tag == "TotalTimeSeconds" and el.text:
            try:
                duration += float(el.text)
            except ValueError:
                pass
    return start, (duration or None)

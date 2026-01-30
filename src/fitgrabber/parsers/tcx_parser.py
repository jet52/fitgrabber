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
    return activity

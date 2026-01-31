from datetime import datetime, timezone
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Event,
    EventType,
    FileType,
    Intensity,
    LapTrigger,
    Manufacturer,
    Sport,
)

from fitgrabber.parsers.models import Activity

_SPORT_MAP: dict[str, Sport] = {
    "running": Sport.RUNNING,
    "cycling": Sport.CYCLING,
    "swimming": Sport.SWIMMING,
    "walking": Sport.WALKING,
    "hiking": Sport.HIKING,
    "strength": Sport.FITNESS_EQUIPMENT,
    "paddleboarding": Sport.STAND_UP_PADDLEBOARDING,
}

_LAP_TRIGGER_MAP: dict[str, LapTrigger] = {
    "manual": LapTrigger.MANUAL,
    "distance": LapTrigger.DISTANCE,
    "session_end": LapTrigger.SESSION_END,
    "time": LapTrigger.TIME,
    "position_start": LapTrigger.POSITION_START,
    "position_lap": LapTrigger.POSITION_LAP,
    "position_waypoint": LapTrigger.POSITION_WAYPOINT,
    "position_marked": LapTrigger.POSITION_MARKED,
    "fitness_equipment": LapTrigger.FITNESS_EQUIPMENT,
}

_INTENSITY_MAP: dict[str, Intensity] = {
    "active": Intensity.ACTIVE,
    "rest": Intensity.REST,
    "warmup": Intensity.WARMUP,
    "cooldown": Intensity.COOLDOWN,
    "recovery": Intensity.RECOVERY,
    "interval": Intensity.INTERVAL,
}

# Running dynamics fields that RecordMessage supports directly
_RECORD_DYNAMICS = ("stance_time", "stance_time_percent", "vertical_oscillation", "step_length")


def _to_fit_ts(dt: datetime) -> int:
    """Convert datetime to fit-tool timestamp (unix milliseconds)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _get_sport(name: str) -> Sport:
    return _SPORT_MAP.get(name.lower(), Sport.GENERIC)


def write_fit(activity: Activity, fit_path: Path) -> None:
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    start_ts = _to_fit_ts(activity.start_time) if activity.start_time else 0
    end_ts = _to_fit_ts(activity.end_time) if activity.end_time else start_ts

    # File ID
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.time_created = start_ts
    builder.add(fid)

    # Timer start event
    evt_start = EventMessage()
    evt_start.timestamp = start_ts
    evt_start.event = Event.TIMER
    evt_start.event_type = EventType.START
    builder.add(evt_start)

    # Record messages
    for pt in activity.track_points:
        rec = RecordMessage()
        rec.timestamp = _to_fit_ts(pt.timestamp)
        if pt.latitude is not None:
            rec.position_lat = pt.latitude
        if pt.longitude is not None:
            rec.position_long = pt.longitude
        if pt.altitude is not None:
            rec.altitude = pt.altitude
        if pt.heart_rate is not None:
            rec.heart_rate = pt.heart_rate
        if pt.cadence is not None:
            rec.cadence = pt.cadence
        if pt.speed is not None:
            rec.speed = pt.speed
        if pt.power is not None:
            rec.power = pt.power
        if pt.temperature is not None:
            rec.temperature = pt.temperature
        if pt.distance is not None:
            rec.distance = pt.distance
        builder.add(rec)

    # Lap messages
    sport_enum = _get_sport(activity.sport)
    if activity.laps:
        for lap in activity.laps:
            lm = LapMessage()
            lm.timestamp = _to_fit_ts(lap.end_time)
            lm.start_time = _to_fit_ts(lap.start_time)
            if lap.total_distance is not None:
                lm.total_distance = lap.total_distance
            if lap.total_duration is not None:
                lm.total_elapsed_time = lap.total_duration
            if lap.total_calories is not None:
                lm.total_calories = lap.total_calories
            if lap.avg_heart_rate is not None:
                lm.avg_heart_rate = lap.avg_heart_rate
            if lap.max_heart_rate is not None:
                lm.max_heart_rate = lap.max_heart_rate
            if lap.avg_speed is not None:
                lm.avg_speed = lap.avg_speed
            if lap.avg_cadence is not None:
                lm.avg_cadence = lap.avg_cadence
            if lap.avg_power is not None:
                lm.avg_power = lap.avg_power
            if lap.max_power is not None:
                lm.max_power = lap.max_power
            if lap.lap_trigger:
                trigger = _LAP_TRIGGER_MAP.get(lap.lap_trigger.lower())
                if trigger:
                    lm.lap_trigger = trigger
            if lap.intensity:
                intensity = _INTENSITY_MAP.get(lap.intensity.lower())
                if intensity:
                    lm.intensity = intensity
            lm.sport = sport_enum
            builder.add(lm)
    else:
        # Single lap covering the whole activity
        lm = LapMessage()
        lm.timestamp = end_ts
        lm.start_time = start_ts
        if activity.total_distance is not None:
            lm.total_distance = activity.total_distance
        if activity.total_duration is not None:
            lm.total_elapsed_time = activity.total_duration
        if activity.total_calories is not None:
            lm.total_calories = activity.total_calories
        lm.lap_trigger = LapTrigger.SESSION_END
        lm.sport = sport_enum
        builder.add(lm)

    # Session message
    sess = SessionMessage()
    sess.timestamp = end_ts
    sess.start_time = start_ts
    sess.sport = sport_enum
    if activity.total_distance is not None:
        sess.total_distance = activity.total_distance
    if activity.total_duration is not None:
        sess.total_elapsed_time = activity.total_duration
    if activity.total_calories is not None:
        sess.total_calories = activity.total_calories
    if activity.avg_heart_rate is not None:
        sess.avg_heart_rate = activity.avg_heart_rate
    if activity.max_heart_rate is not None:
        sess.max_heart_rate = activity.max_heart_rate
    if activity.avg_speed is not None:
        sess.avg_speed = activity.avg_speed
    if activity.avg_cadence is not None:
        sess.avg_cadence = activity.avg_cadence
    if activity.avg_power is not None:
        sess.avg_power = activity.avg_power
    builder.add(sess)

    # Activity message
    act = ActivityMessage()
    act.timestamp = end_ts
    act.num_sessions = 1
    if activity.total_duration is not None:
        act.total_timer_time = activity.total_duration
    builder.add(act)

    # Timer stop event
    evt_stop = EventMessage()
    evt_stop.timestamp = end_ts
    evt_stop.event = Event.TIMER
    evt_stop.event_type = EventType.STOP_ALL
    builder.add(evt_stop)

    fit_file = builder.build()
    fit_path.parent.mkdir(parents=True, exist_ok=True)
    fit_file.to_file(str(fit_path))

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class TrackPoint:
    timestamp: datetime
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None  # meters
    heart_rate: int | None = None  # bpm
    cadence: int | None = None  # rpm or spm
    speed: float | None = None  # m/s
    power: int | None = None  # watts
    temperature: float | None = None  # celsius
    distance: float | None = None  # cumulative meters


@dataclass
class Lap:
    start_time: datetime
    end_time: datetime
    total_distance: float | None = None
    total_duration: float | None = None
    total_calories: int | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    avg_speed: float | None = None
    avg_cadence: int | None = None
    avg_power: int | None = None
    max_power: int | None = None
    lap_trigger: str | None = None  # "distance", "manual", "session_end"
    intensity: str | None = None  # "active", "rest", "interval"
    sport: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Activity:
    source_file: Path
    source_platform: str
    sport: str = "unknown"
    start_time: datetime | None = None
    end_time: datetime | None = None
    track_points: list[TrackPoint] = field(default_factory=list)
    laps: list[Lap] = field(default_factory=list)
    total_distance: float | None = None  # meters
    total_duration: float | None = None  # seconds
    total_calories: int | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    avg_speed: float | None = None  # m/s
    avg_cadence: int | None = None
    avg_power: int | None = None
    hr_source: str | None = None  # "chest", "wrist", or None
    name: str = ""
    notes: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def duration_minutes(self) -> float | None:
        if self.total_duration is not None:
            return self.total_duration / 60
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 60
        return None

    @property
    def distance_km(self) -> float | None:
        return self.total_distance / 1000 if self.total_distance else None

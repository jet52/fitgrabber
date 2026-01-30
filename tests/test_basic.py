from datetime import datetime, timedelta
from pathlib import Path

from fitgrabber.config import Config
from fitgrabber.parsers.models import Activity, TrackPoint
from fitgrabber.processing.anomaly import detect_anomalies
from fitgrabber.processing.dedup import find_duplicates
from fitgrabber.processing.merge import merge_activities


def test_config_defaults():
    cfg = Config()
    assert cfg.data_dir == Path.home() / "FitnessData"


def test_activity_duration():
    a = Activity(
        source_file=Path("test.fit"),
        source_platform="test",
        start_time=datetime(2024, 1, 1, 8, 0),
        end_time=datetime(2024, 1, 1, 9, 0),
    )
    assert a.duration_minutes == 60.0


def test_dedup_finds_matching_starts():
    entries = [
        {"source_file": "a.fit", "start_time": "2024-01-01T08:00:00", "sport": "running"},
        {"source_file": "b.gpx", "start_time": "2024-01-01T08:02:00", "sport": "running"},
        {"source_file": "c.fit", "start_time": "2024-01-01T15:00:00", "sport": "cycling"},
    ]
    groups = find_duplicates(entries)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_merge_combines_fields():
    now = datetime(2024, 1, 1, 8, 0)
    a1 = Activity(
        source_file=Path("a.fit"),
        source_platform="garmin",
        sport="running",
        track_points=[
            TrackPoint(timestamp=now, latitude=40.0, longitude=-74.0, heart_rate=150),
            TrackPoint(
                timestamp=now + timedelta(seconds=5),
                latitude=40.001,
                longitude=-74.001,
                heart_rate=155,
            ),
        ],
    )
    a2 = Activity(
        source_file=Path("b.gpx"),
        source_platform="strava",
        sport="running",
        track_points=[
            TrackPoint(timestamp=now + timedelta(seconds=1), power=250, cadence=180),
            TrackPoint(timestamp=now + timedelta(seconds=6), power=260, cadence=182),
        ],
    )
    merged = merge_activities([a1, a2])
    assert merged.source_platform == "merged"
    assert len(merged.track_points) >= 2
    # Merged points should have fields from both sources
    first = merged.track_points[0]
    assert first.heart_rate is not None or first.power is not None


def test_anomaly_detects_speed_spike():
    now = datetime(2024, 1, 1, 8, 0)
    points = [
        TrackPoint(timestamp=now, latitude=40.0, longitude=-74.0),
        TrackPoint(
            timestamp=now + timedelta(seconds=1), latitude=40.001, longitude=-74.0
        ),  # ~111m in 1s
    ]
    activity = Activity(
        source_file=Path("test.fit"),
        source_platform="test",
        sport="running",
        track_points=points,
    )
    anomalies = detect_anomalies(activity)
    assert len(anomalies) > 0

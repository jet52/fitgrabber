from datetime import datetime, timedelta
from pathlib import Path

from fitgrabber.config import Config
from fitgrabber.parsers.models import Activity, Lap, TrackPoint
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


def test_fit_writer_roundtrip(tmp_path):
    """Write a FIT file and verify it can be parsed back with records, laps, session."""
    from fitgrabber.export.fit_writer import write_fit

    now = datetime(2024, 1, 1, 8, 0)
    activity = Activity(
        source_file=Path("test.fit"),
        source_platform="merged",
        sport="running",
        start_time=now,
        end_time=now + timedelta(minutes=30),
        total_distance=5000.0,
        total_duration=1800.0,
        total_calories=300,
        avg_heart_rate=155,
        max_heart_rate=175,
        avg_speed=2.78,
        track_points=[
            TrackPoint(
                timestamp=now + timedelta(seconds=i * 10),
                latitude=40.0 + i * 0.0001,
                longitude=-74.0,
                heart_rate=150 + i,
                distance=float(i * 28),
                speed=2.78,
                altitude=50.0,
            )
            for i in range(10)
        ],
        laps=[
            Lap(
                start_time=now,
                end_time=now + timedelta(minutes=15),
                total_distance=2500.0,
                total_duration=900.0,
                avg_heart_rate=150,
                max_heart_rate=170,
                lap_trigger="distance",
                intensity="active",
            ),
            Lap(
                start_time=now + timedelta(minutes=15),
                end_time=now + timedelta(minutes=30),
                total_distance=2500.0,
                total_duration=900.0,
                avg_heart_rate=160,
                max_heart_rate=175,
                lap_trigger="session_end",
                intensity="active",
            ),
        ],
    )

    fit_path = tmp_path / "test_output.fit"
    write_fit(activity, fit_path)
    assert fit_path.exists()
    assert fit_path.stat().st_size > 100

    # Parse back with fitdecode
    import fitdecode

    msg_types: dict[str, int] = {}
    with fitdecode.FitReader(str(fit_path)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                msg_types[frame.name] = msg_types.get(frame.name, 0) + 1

    assert msg_types.get("record", 0) == 10
    assert msg_types.get("lap", 0) == 2
    assert msg_types.get("session", 0) == 1
    assert msg_types.get("activity", 0) == 1
    assert msg_types.get("event", 0) == 2  # start + stop


def test_merge_selects_best_laps():
    now = datetime(2024, 1, 1, 8, 0)
    a1 = Activity(
        source_file=Path("a.fit"),
        source_platform="garmin",
        sport="running",
        track_points=[TrackPoint(timestamp=now)],
        laps=[
            Lap(start_time=now, end_time=now + timedelta(minutes=5), lap_trigger="manual"),
            Lap(
                start_time=now + timedelta(minutes=5),
                end_time=now + timedelta(minutes=10),
                lap_trigger="manual",
            ),
        ],
    )
    a2 = Activity(
        source_file=Path("b.fit"),
        source_platform="strava",
        sport="running",
        track_points=[TrackPoint(timestamp=now)],
        laps=[
            Lap(
                start_time=now,
                end_time=now + timedelta(minutes=10),
                lap_trigger="session_end",
            ),
        ],
    )
    merged = merge_activities([a1, a2])
    # Should pick garmin's manual laps over strava's session_end
    assert len(merged.laps) == 2
    assert merged.laps[0].lap_trigger == "manual"


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

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


def test_merge_recomputes_lap_power_from_canonical_stream():
    now = datetime(2024, 1, 1, 8, 0)
    # Lap-source records carry native power (200W) in the lap summary, but the
    # canonical merged record stream (with Stryd power 150W) should win.
    pts = [
        TrackPoint(timestamp=now + timedelta(seconds=s), heart_rate=140, power=150)
        for s in range(0, 300, 10)
    ]
    a1 = Activity(
        source_file=Path("a.fit"),
        source_platform="garmin",
        sport="running",
        power_source="stryd",
        power_source_alt="garmin_native",
        track_points=pts,
        laps=[
            Lap(
                start_time=now,
                end_time=now,  # degenerate end (broken Garmin lap timestamp)
                total_duration=300,
                lap_trigger="manual",
                avg_power=200,  # stale native lap power
                max_power=260,
            ),
        ],
    )
    a2 = Activity(
        source_file=Path("b.fit"),
        source_platform="strava",
        sport="running",
        track_points=[TrackPoint(timestamp=now, heart_rate=140)],
        laps=[Lap(start_time=now, end_time=now + timedelta(minutes=5), lap_trigger="session_end")],
    )
    merged = merge_activities([a1, a2])
    assert merged.laps[0].avg_power == 150
    assert merged.laps[0].max_power == 150


def test_ts_in_window_scopes_prune_to_date_range():
    from fitgrabber.cli import _ts_in_window

    after = datetime(2026, 6, 13)
    before = datetime(2026, 6, 14)
    inside = "20260613_154616_running_merged.fit"
    before_window = "20260101_080000_running_merged.fit"
    after_window = "20260620_080000_running_merged.fit"
    assert _ts_in_window(inside, after, before) is True
    assert _ts_in_window(before_window, after, before) is False
    assert _ts_in_window(after_window, after, before) is False
    # before is exclusive, after inclusive (matches _filter_by_date)
    assert _ts_in_window("20260614_000000_x.fit", after, before) is False
    assert _ts_in_window("20260613_000000_x.fit", after, before) is True
    # unparseable prefix is never a prune candidate
    assert _ts_in_window("notimestamp.json", after, before) is False


def test_resolve_power_source():
    from fitgrabber.parsers.fit_parser import _resolve_power_source

    # Garmin FIT with both Stryd dev power and native power → Stryd canonical
    assert _resolve_power_source("garmin", saw_stryd=True, saw_native=True) == (
        "stryd",
        "garmin_native",
    )
    # Garmin with only native running power
    assert _resolve_power_source("garmin", saw_stryd=False, saw_native=True) == (
        "garmin_native",
        None,
    )
    # Standalone Stryd file (native power field is Stryd's own)
    assert _resolve_power_source("stryd", saw_stryd=False, saw_native=True) == ("stryd", None)
    # No power at all
    assert _resolve_power_source("garmin", saw_stryd=False, saw_native=False) == (None, None)


def test_detect_hr_source():
    from fitgrabber.parsers.fit_parser import _detect_hr_source

    baro = {"device_type": "barometer", "ant_device_type": None, "source_type": "local"}
    whr = {"device_type": "whr", "ant_device_type": None, "source_type": "local"}
    ble_strap = {
        "device_type": "heart_rate",
        "ant_device_type": None,
        "source_type": "bluetooth_low_energy",
    }
    ant_strap = {"device_type": None, "ant_device_type": 120, "source_type": "antplus"}

    # Barometer must not be mistaken for an HR sensor (the old bug)
    assert _detect_hr_source([baro, whr]) == "wrist"
    # External strap wins over wrist optical
    assert _detect_hr_source([baro, ble_strap, whr]) == "chest"
    assert _detect_hr_source([ant_strap, whr]) == "chest"
    assert _detect_hr_source([baro]) is None


def test_merge_propagates_provenance():
    now = datetime(2024, 1, 1, 8, 0)
    garmin = Activity(
        source_file=Path("a.fit"),
        source_platform="garmin",
        sport="running",
        hr_source="chest",
        hr_detail="rr",
        power_source="stryd",
        power_source_alt="garmin_native",
        rr_intervals=[0.5, 0.51, 0.49],
        track_points=[TrackPoint(timestamp=now, heart_rate=150, power=260)],
    )
    strava = Activity(
        source_file=Path("b.json"),
        source_platform="strava",
        sport="running",
        power_source="garmin",
        track_points=[TrackPoint(timestamp=now, power=260)],
    )
    merged = merge_activities([garmin, strava])
    assert merged.power_source == "stryd"
    assert merged.power_source_alt == "garmin_native"
    assert merged.hr_source == "chest"
    assert merged.hr_detail == "rr"
    assert merged.rr_intervals == [0.5, 0.51, 0.49]


def test_write_sidecar(tmp_path):
    from fitgrabber.export.sidecar import sidecar_path, write_sidecar

    activity = Activity(
        source_file=Path("x.fit"),
        source_platform="merged",
        sport="running",
        power_source="stryd",
        power_source_alt="garmin_native",
        hr_source="chest",
        hr_detail="rr",
        rr_intervals=[0.545, 0.539],
        metadata={"sources": [{"platform": "garmin", "power_source": "stryd"}]},
    )
    fit_path = tmp_path / "20260606_140048_running_merged.fit"
    out = write_sidecar(activity, fit_path)
    assert out == sidecar_path(fit_path)
    assert out.name == "20260606_140048_running_merged.meta.json"

    import json

    data = json.loads(out.read_text())
    assert data["power_source"] == "stryd"
    assert data["power_source_alt"] == "garmin_native"
    assert data["hr_source"] == "chest"
    assert data["has_rr"] is True
    assert data["rr_ms"] == [545, 539]


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

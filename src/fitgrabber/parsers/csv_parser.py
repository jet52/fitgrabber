from pathlib import Path

import pandas as pd

from fitgrabber.parsers.models import Activity, TrackPoint

# Common column name mappings
TIMESTAMP_COLS = ["timestamp", "time", "datetime", "date_time", "start_time"]
LAT_COLS = ["latitude", "lat", "position_lat"]
LON_COLS = ["longitude", "lon", "lng", "position_long"]
ALT_COLS = ["altitude", "elevation", "alt"]
HR_COLS = ["heart_rate", "hr", "heartrate"]
CADENCE_COLS = ["cadence", "cad"]
SPEED_COLS = ["speed", "velocity"]
POWER_COLS = ["power", "watts"]
DISTANCE_COLS = ["distance", "dist"]


def parse(filepath: Path, platform: str = "unknown") -> Activity:
    df = pd.read_csv(filepath)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    activity = Activity(source_file=filepath, source_platform=platform)
    points: list[TrackPoint] = []

    ts_col = _find_col(df, TIMESTAMP_COLS)
    if ts_col is None:
        return activity

    for _, row in df.iterrows():
        try:
            ts = pd.to_datetime(row[ts_col])
        except Exception:
            continue
        points.append(
            TrackPoint(
                timestamp=ts.to_pydatetime(),
                latitude=_get_val(row, LAT_COLS, float),
                longitude=_get_val(row, LON_COLS, float),
                altitude=_get_val(row, ALT_COLS, float),
                heart_rate=_get_val(row, HR_COLS, int),
                cadence=_get_val(row, CADENCE_COLS, int),
                speed=_get_val(row, SPEED_COLS, float),
                power=_get_val(row, POWER_COLS, int),
                distance=_get_val(row, DISTANCE_COLS, float),
            )
        )

    activity.track_points = points
    if points:
        activity.start_time = points[0].timestamp
        activity.end_time = points[-1].timestamp
    return activity


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _get_val(row, candidates: list[str], dtype: type):
    for c in candidates:
        if c in row.index:
            v = row[c]
            if pd.notna(v):
                try:
                    return dtype(v)
                except (ValueError, TypeError):
                    pass
    return None

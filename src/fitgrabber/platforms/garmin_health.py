import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import typer
from garminconnect import Garmin

from fitgrabber.config import Config

logging.getLogger("garminconnect").setLevel(logging.CRITICAL)
logging.getLogger("garth").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.CRITICAL)

HEALTH_METHODS: list[tuple[str, str, bool]] = [
    # (key, method_name, uses_date_range)
    ("weight", "get_daily_weigh_ins", False),
    ("heart_rates", "get_heart_rates", False),
    ("sleep", "get_sleep_data", False),
    ("stress", "get_all_day_stress", False),
    ("body_battery", "get_body_battery", True),
    ("hrv", "get_hrv_data", False),
    ("training_readiness", "get_training_readiness", False),
    ("training_status", "get_training_status", False),
    ("spo2", "get_spo2_data", False),
    ("respiration", "get_respiration_data", False),
    ("steps", "get_daily_steps", True),
    ("max_metrics", "get_max_metrics", False),
    ("endurance_score", "get_endurance_score", True),
    ("body_composition", "get_body_composition", True),
    ("hydration", "get_hydration_data", False),
]


def sync(cfg: Config, platform: str = "garmin-health") -> list[Path]:
    """Download daily health metrics from Garmin Connect."""
    creds = cfg.platforms.get("garmin", {})
    email = creds.get("email")
    password = creds.get("password")
    if not email or not password:
        raise RuntimeError(
            "Garmin credentials not configured. Set platforms.garmin.email and .password in config."
        )

    health_cfg = cfg.platforms.get("garmin-health", {})
    days = health_cfg.get("days", 365)

    dest = cfg.raw_dir("garmin-health")
    dest.mkdir(parents=True, exist_ok=True)

    force = health_cfg.get("force", False)

    client = Garmin(email, password)
    client.login()

    today = date.today()
    dates = [today - timedelta(days=i) for i in range(days)]
    total = len(dates)
    saved: list[Path] = []

    try:
        for i, d in enumerate(dates, 1):
            date_str = d.isoformat()
            out_path = dest / f"{date_str}.json"

            if out_path.exists() and not force:
                continue

            data: dict = {"date": date_str}
            fetched: list[str] = []

            for key, method_name, is_range in HEALTH_METHODS:
                try:
                    fn = getattr(client, method_name)
                    if is_range:
                        result = fn(date_str, date_str)
                    else:
                        result = fn(date_str)
                    data[key] = result
                    fetched.append(key)
                except Exception:
                    data[key] = None

            typer.echo(f"[{i}/{total}] {date_str} — {', '.join(fetched)}")
            out_path.write_text(json.dumps(data, indent=2, default=str))
            saved.append(out_path)

            if i < total:
                time.sleep(1)
    except KeyboardInterrupt:
        typer.echo(f"\nStopped early. Saved {len(saved)} files.")

    return saved

import json
import logging
import signal
import time
from collections import deque
from pathlib import Path

import typer

from fitgrabber.config import Config

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
DELAY_BETWEEN_ACTIVITIES = 3.0  # seconds
RATE_WINDOW = 15 * 60  # 15 minutes in seconds
RATE_LIMIT = 90  # pause before hitting Strava's 100/15min limit


class _RateTracker:
    """Track API requests within a sliding 15-minute window."""

    def __init__(self, window: float = RATE_WINDOW, limit: int = RATE_LIMIT):
        self.window = window
        self.limit = limit
        self.timestamps: deque[float] = deque()

    def _prune(self):
        cutoff = time.time() - self.window
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def record(self):
        self.timestamps.append(time.time())

    def wait_if_needed(self):
        """Block until we're under the rate limit."""
        self._prune()
        if len(self.timestamps) >= self.limit:
            oldest = self.timestamps[0]
            wait = oldest + self.window - time.time() + 1
            if wait > 0:
                count = len(self.timestamps)
                typer.echo(f"  Rate limit: {count} reqs in window. Pausing {wait:.0f}s...")
                time.sleep(wait)
                self._prune()


# Suppress noisy stravalib/root warnings
logging.getLogger("stravalib").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout("Request timed out")


def _to_seconds(val) -> float:
    if val is None:
        return 0
    if hasattr(val, "total_seconds"):
        return val.total_seconds()
    if hasattr(val, "magnitude"):
        return float(val.magnitude)
    return float(val)


STREAM_TYPES = [
    "time",
    "latlng",
    "distance",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "temp",
    "moving",
    "grade_smooth",
]


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return "429" in msg or "rate limit" in msg


# Summary fields pulled from the activity detail object (no extra API calls).
SUMMARY_FIELDS = (
    "calories",
    "average_heartrate",
    "max_heartrate",
    "average_speed",
    "max_speed",
    "device_name",
    "external_id",
)


def _detail_summary(detail) -> dict:
    """Extract summary metrics + device info from a stravalib activity detail."""
    avg_speed = getattr(detail, "average_speed", None)
    max_speed = getattr(detail, "max_speed", None)
    return {
        "calories": getattr(detail, "calories", None),
        "average_heartrate": getattr(detail, "average_heartrate", None),
        "max_heartrate": getattr(detail, "max_heartrate", None),
        "average_speed": float(avg_speed) if avg_speed is not None else None,
        "max_speed": float(max_speed) if max_speed is not None else None,
        "device_name": getattr(detail, "device_name", None),
        "external_id": getattr(detail, "external_id", None),
    }


def _refresh_token(cfg: Config) -> str:
    import requests

    creds = cfg.platforms.get("strava", {})
    refresh_token = creds.get("refresh_token")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    if not all([refresh_token, client_id, client_secret]):
        raise RuntimeError(
            "Strava refresh requires client_id, client_secret, and refresh_token in config."
        )

    typer.echo("  Refreshing Strava access token...")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    creds["access_token"] = data["access_token"]
    creds["refresh_token"] = data["refresh_token"]
    creds["expires_at"] = data["expires_at"]
    cfg.save()

    typer.echo("  Token refreshed.")
    return data["access_token"]


def _get_access_token(cfg: Config) -> str:
    creds = cfg.platforms.get("strava", {})
    access_token = creds.get("access_token")
    expires_at = creds.get("expires_at", 0)

    if access_token and time.time() < expires_at - 60:
        return access_token

    if creds.get("refresh_token"):
        return _refresh_token(cfg)

    if access_token:
        return access_token

    raise RuntimeError("No Strava access_token or refresh_token configured.")


def _fetch_activities_with_retry(client, rate: _RateTracker, max_retries: int = 3) -> list:
    """Fetch activity list with rate limit retry."""
    for attempt in range(max_retries):
        try:
            rate.wait_if_needed()
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(120)  # 2 min timeout for full list
            activities = list(client.get_activities())
            signal.alarm(0)
            rate.record()
            return activities
        except _Timeout:
            signal.alarm(0)
            typer.echo("  Activity list fetch timed out.")
            if attempt < max_retries - 1:
                wait = 60 * (attempt + 1)
                typer.echo(f"  Retrying in {wait}s...")
                time.sleep(wait)
        except Exception as e:
            signal.alarm(0)
            if _is_rate_limit(e):
                wait = 15 * 60  # 15 minutes
                typer.echo("  Rate limited fetching activity list.")
                typer.echo(f"  Waiting {wait // 60} minutes...")
                time.sleep(wait)
            else:
                typer.echo(f"  Error fetching activities: {e}")
                if attempt < max_retries - 1:
                    time.sleep(30)
                else:
                    raise
    return []


def _fetch_activity_with_retry(
    client, activity_id: int, rate: _RateTracker, max_retries: int = 3
) -> dict | None:
    """Fetch activity detail + streams, retrying on rate limit."""
    for attempt in range(max_retries):
        try:
            rate.wait_if_needed()
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(REQUEST_TIMEOUT)
            detail = client.get_activity(activity_id)
            signal.alarm(0)
            rate.record()

            record: dict = {
                "id": activity_id,
                "name": detail.name,
                "sport_type": str(detail.sport_type),
                "start_date": str(detail.start_date),
                "elapsed_time": _to_seconds(detail.elapsed_time),
                "moving_time": _to_seconds(detail.moving_time),
                "distance": float(detail.distance) if detail.distance else 0,
                **_detail_summary(detail),
            }

            try:
                rate.wait_if_needed()
                signal.alarm(REQUEST_TIMEOUT)
                streams = client.get_activity_streams(activity_id, types=STREAM_TYPES)
                signal.alarm(0)
                rate.record()
                record["streams"] = {k: v.data for k, v in streams.items()}
            except _Timeout:
                signal.alarm(0)
                typer.echo("    Streams timed out, saving without.")
                record["streams"] = {}
            except Exception as e:
                signal.alarm(0)
                if _is_rate_limit(e):
                    raise  # let outer handler deal with it
                typer.echo(f"    Streams unavailable: {e}")
                record["streams"] = {}

            return record

        except _Timeout:
            signal.alarm(0)
            typer.echo(f"    Timed out (attempt {attempt + 1})...")
            time.sleep(5)
        except Exception as e:
            signal.alarm(0)
            if _is_rate_limit(e):
                wait = 15 * 60
                typer.echo(f"    Rate limited. Waiting {wait // 60} minutes...")
                time.sleep(wait)
            else:
                typer.echo(f"    Failed: {e}")
                return None

    typer.echo("    Gave up after retries.")
    return None


def sync(cfg: Config, platform: str = "strava") -> list[Path]:
    """Download activities from Strava as JSON (metadata + streams)."""
    from stravalib.client import Client

    token = _get_access_token(cfg)
    dest = cfg.raw_dir("strava")
    dest.mkdir(parents=True, exist_ok=True)

    client = Client(access_token=token)
    rate = _RateTracker()
    typer.echo("  Fetching activity list...")
    activities = _fetch_activities_with_retry(client, rate)
    if not activities:
        typer.echo("  No activities fetched.")
        return []
    typer.echo(f"  Found {len(activities)} activities.")

    # Filter to only those needing download
    to_download = []
    skipped = 0
    for activity in activities:
        filepath = dest / f"{activity.id}.json"
        if filepath.exists():
            skipped += 1
        else:
            to_download.append(activity)

    typer.echo(f"  Need to download: {len(to_download)}")
    typer.echo(f"  Already have: {skipped}")

    downloaded: list[Path] = []

    try:
        for i, activity in enumerate(to_download, 1):
            filepath = dest / f"{activity.id}.json"
            name = activity.name or "unnamed"
            date_str = str(activity.start_date)[:10] if activity.start_date else ""
            typer.echo(f"  [{i}/{len(to_download)}] {date_str} {name} ({activity.id})")

            time.sleep(DELAY_BETWEEN_ACTIVITIES)

            record = _fetch_activity_with_retry(client, activity.id, rate)
            if record is None:
                continue

            filepath.write_text(json.dumps(record, indent=2, default=str))
            downloaded.append(filepath)
    except KeyboardInterrupt:
        typer.echo("\n  Interrupted! Progress saved (already-downloaded files are kept).")

    typer.echo("\n  Summary:")
    typer.echo(f"    Downloaded: {len(downloaded)}")
    typer.echo(f"    Already had: {skipped}")
    return downloaded


def backfill_summary(cfg: Config) -> int:
    """Backfill summary metrics + device info into existing Strava JSON files.

    Fills any file missing one or more of SUMMARY_FIELDS (calories, avg/max HR,
    avg/max speed, device_name, external_id). One detail fetch per file; streams
    are left untouched.
    """
    from stravalib.client import Client

    token = _get_access_token(cfg)
    dest = cfg.raw_dir("strava")
    if not dest.exists():
        typer.echo("  No strava directory found.")
        return 0

    client = Client(access_token=token)
    rate = _RateTracker()

    needs_update = []
    for f in sorted(dest.iterdir()):
        if f.suffix != ".json":
            continue
        data = json.loads(f.read_text())
        if any(k not in data for k in SUMMARY_FIELDS):
            needs_update.append((f, data))

    if not needs_update:
        typer.echo("  All files already have summary metrics.")
        return 0

    typer.echo(f"  {len(needs_update)} files need summary backfill.")
    updated = 0

    try:
        for i, (filepath, data) in enumerate(needs_update, 1):
            activity_id = data.get("id")
            if not activity_id:
                continue
            name = data.get("name", "unnamed")
            typer.echo(f"  [{i}/{len(needs_update)}] {name} ({activity_id})")

            time.sleep(DELAY_BETWEEN_ACTIVITIES)
            try:
                rate.wait_if_needed()
                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(REQUEST_TIMEOUT)
                detail = client.get_activity(activity_id)
                signal.alarm(0)
                rate.record()

                data.update(_detail_summary(detail))
                filepath.write_text(json.dumps(data, indent=2, default=str))
                updated += 1
            except _Timeout:
                signal.alarm(0)
                typer.echo("    Timed out, skipping.")
            except Exception as e:
                signal.alarm(0)
                if _is_rate_limit(e):
                    wait = 15 * 60
                    typer.echo(f"    Rate limited. Waiting {wait // 60} minutes...")
                    time.sleep(wait)
                else:
                    typer.echo(f"    Failed: {e}")
    except KeyboardInterrupt:
        typer.echo("\n  Interrupted! Progress saved.")

    typer.echo(f"  Updated {updated}/{len(needs_update)} files.")
    return updated

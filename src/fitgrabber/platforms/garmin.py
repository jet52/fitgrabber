import json
import logging
import time
from pathlib import Path

import typer
from garminconnect import Garmin

from fitgrabber.config import Config

MAX_RETRIES = 2
ERRORS_BEFORE_PAUSE = 5

# Suppress noisy library tracebacks
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)
logging.getLogger("garth").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.CRITICAL)


def _is_server_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ["500", "429", "max retries", "too many", "connection"])


def _error_summary(e: Exception) -> str:
    msg = str(e)
    for marker in ["Caused by ", "ResponseError("]:
        if marker in msg:
            start = msg.index(marker) + len(marker)
            msg = msg[start:].strip(")'\"")
            break
    if len(msg) > 120:
        msg = msg[:120] + "..."
    return f"{type(e).__name__}: {msg}"


def _load_skip_list(dest: Path) -> set[str]:
    skip_file = dest / ".failed_activities.json"
    if skip_file.exists():
        try:
            return set(json.loads(skip_file.read_text()))
        except Exception:
            pass
    return set()


def _save_skip_list(dest: Path, skip_ids: set[str]) -> None:
    skip_file = dest / ".failed_activities.json"
    skip_file.write_text(json.dumps(sorted(skip_ids), indent=2))


def _download_one(client, activity_id: int) -> tuple[bytes | None, str]:
    """Try downloading in original format, fall back to TCX, then GPX."""
    formats = [
        (client.ActivityDownloadFormat.ORIGINAL, ".zip"),
        (client.ActivityDownloadFormat.TCX, ".tcx"),
        (client.ActivityDownloadFormat.GPX, ".gpx"),
    ]
    last_error = ""
    for fmt_idx, (dl_fmt, ext) in enumerate(formats):
        if fmt_idx > 0:
            typer.echo(f"    Falling back to {ext}...")
            time.sleep(5)

        for attempt in range(MAX_RETRIES):
            try:
                data = client.download_activity(activity_id, dl_fmt=dl_fmt)
                if fmt_idx > 0:
                    typer.echo(f"    {ext} succeeded!")
                return data, ext
            except Exception as e:
                last_error = _error_summary(e)
                typer.echo(f"    {ext}: {last_error}")
                if _is_server_error(e) and attempt < MAX_RETRIES - 1:
                    wait = 10 * (attempt + 1)
                    typer.echo(f"    Retrying {ext} in {wait}s...")
                    time.sleep(wait)
                else:
                    break  # try next format

    typer.echo("    All formats failed.")
    return None, last_error


def sync(cfg: Config, platform: str = "garmin") -> list[Path]:
    """Download activities from Garmin Connect."""
    creds = cfg.platforms.get("garmin", {})
    email = creds.get("email")
    password = creds.get("password")
    if not email or not password:
        raise RuntimeError(
            "Garmin credentials not configured. "
            "Set platforms.garmin.email and .password in config."
        )

    dest = cfg.raw_dir("garmin")
    dest.mkdir(parents=True, exist_ok=True)

    skip_ids = _load_skip_list(dest)
    if skip_ids:
        typer.echo(f"  Skipping {len(skip_ids)} previously failed activities.")

    typer.echo("  Logging in to Garmin Connect...")
    client = Garmin(email, password)
    client.login()

    typer.echo("  Fetching activity list...")
    activities: list[dict] = []
    page_size = 200
    start = 0
    while True:
        page = client.get_activities(start, page_size)
        if not page:
            break
        activities.extend(page)
        typer.echo(f"    Fetched {len(activities)} activities so far...")
        start += page_size
    typer.echo(f"  Found {len(activities)} activities total.")

    # Build list of activities that need downloading
    to_download: list[dict] = []
    skipped = 0
    skipped_known_bad = 0
    for activity in activities:
        activity_id = activity["activityId"]
        existing = [
            f
            for f in dest.glob(f"{activity_id}.*")
            if f.suffix in (".zip", ".tcx", ".gpx")
        ]
        if existing:
            skipped += 1
        elif str(activity_id) in skip_ids:
            skipped_known_bad += 1
        else:
            to_download.append(activity)

    typer.echo(f"  Need to download: {len(to_download)}")
    typer.echo(f"  Already have: {skipped}")
    if skipped_known_bad:
        typer.echo(f"  Skipping {skipped_known_bad} previously failed.")

    downloaded: list[Path] = []
    failed: list[str] = []
    consecutive_errors = 0

    try:
        for i, activity in enumerate(to_download, 1):
            activity_id = activity["activityId"]

            time.sleep(1.0)

            if consecutive_errors >= ERRORS_BEFORE_PAUSE:
                wait = 120
                typer.echo(f"  {consecutive_errors} consecutive errors, pausing {wait}s...")
                time.sleep(wait)
                consecutive_errors = 0

            name = activity.get("activityName", "unnamed")
            date_str = activity.get("startTimeLocal", "")[:10]
            typer.echo(f"  [{i}/{len(to_download)}] {date_str} {name} ({activity_id})")

            data, ext_or_error = _download_one(client, activity_id)
            if data is None:
                failed.append(f"{activity_id} ({name})")
                skip_ids.add(str(activity_id))
                consecutive_errors += 1
                continue

            consecutive_errors = 0
            filepath = dest / f"{activity_id}{ext_or_error}"
            filepath.write_bytes(data)
            downloaded.append(filepath)
    except KeyboardInterrupt:
        typer.echo("\n  Interrupted! Saving progress...")
    finally:
        _save_skip_list(dest, skip_ids)

    typer.echo("\n  Summary:")
    typer.echo(f"    Downloaded: {len(downloaded)}")
    typer.echo(f"    Already had: {skipped}")
    if skipped_known_bad:
        typer.echo(f"    Skipped (known bad): {skipped_known_bad}")
    if failed:
        typer.echo(f"    Failed this run: {len(failed)}")
        for f in failed[:10]:
            typer.echo(f"      {f}")
        if len(failed) > 10:
            typer.echo(f"      ... and {len(failed) - 10} more")
        typer.echo("    (Failed IDs saved to .failed_activities.json)")
        typer.echo("    Delete that file to retry them.")
    return downloaded

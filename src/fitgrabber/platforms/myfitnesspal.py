import csv
from datetime import date, timedelta
from pathlib import Path

import typer

from fitgrabber.config import Config


def sync(cfg: Config, platform: str = "myfitnesspal") -> list[Path]:
    """Download nutrition data from MyFitnessPal."""
    import myfitnesspal

    creds = cfg.platforms.get("myfitnesspal", {})
    username = creds.get("username")
    if not username:
        raise RuntimeError(
            "MyFitnessPal username not configured. "
            "Set platforms.myfitnesspal.username in config."
        )

    dest = cfg.raw_dir("myfitnesspal")
    dest.mkdir(parents=True, exist_ok=True)

    typer.echo(f"  Logging in as {username}...")
    client = myfitnesspal.Client(username)

    downloaded: list[Path] = []
    today = date.today()
    days = int(cfg.platforms.get("myfitnesspal", {}).get("days", 365))
    typer.echo(f"  Checking last {days} days...")

    for i in range(days):
        d = today - timedelta(days=i)
        filepath = dest / f"{d.isoformat()}.csv"
        if filepath.exists():
            continue

        if i > 0 and i % 30 == 0:
            typer.echo(f"  [{i}/{days}] Processing {d}...")

        try:
            day_data = client.get_date(d)
            if not day_data.meals:
                continue
            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["meal", "food", "calories", "carbs", "fat", "protein", "sodium", "sugar"]
                )
                for meal in day_data.meals:
                    for entry in meal.entries:
                        n = entry.nutrition_information
                        writer.writerow([
                            meal.name,
                            entry.name,
                            n.get("calories", 0),
                            n.get("carbohydrates", 0),
                            n.get("fat", 0),
                            n.get("protein", 0),
                            n.get("sodium", 0),
                            n.get("sugar", 0),
                        ])
            downloaded.append(filepath)
        except Exception:
            continue

    return downloaded

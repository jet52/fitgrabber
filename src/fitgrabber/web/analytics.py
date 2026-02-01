"""Analytics computations for the web UI."""

from collections import defaultdict
from datetime import datetime


def calendar_data(activities: list[dict], year: int, month: int) -> dict:
    """Build calendar grid data for a given month."""
    import calendar

    cal = calendar.Calendar(firstweekday=6)  # Sunday start
    weeks = cal.monthdatescalendar(year, month)

    # Index activities by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for a in activities:
        ts = a.get("start_time")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts).date()
            by_date[str(dt)].append(a)
        except ValueError:
            continue

    grid = []
    month_dist = 0.0
    month_dur = 0.0
    month_count = 0
    for week in weeks:
        row = []
        week_dist = 0.0
        week_dur = 0.0
        week_count = 0
        for day in week:
            key = str(day)
            day_acts = by_date.get(key, [])
            dist = sum(a.get("total_distance") or 0 for a in day_acts)
            dur = sum(a.get("total_duration") or 0 for a in day_acts)
            sports = list({a.get("sport", "unknown") for a in day_acts})
            row.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "activities": day_acts,
                    "count": len(day_acts),
                    "distance": dist,
                    "duration": dur,
                    "sports": sports,
                }
            )
            if day.month == month:
                week_dist += dist
                week_dur += dur
                week_count += len(day_acts)
        grid.append({"days": row, "distance": week_dist, "duration": week_dur, "count": week_count})
        month_dist += week_dist
        month_dur += week_dur
        month_count += week_count

    return {
        "year": year,
        "month": month,
        "weeks": grid,
        "month_totals": {"distance": month_dist, "duration": month_dur, "count": month_count},
    }


SPORT_COLORS: dict[str, str] = {
    "running": "#e74c3c",
    "cycling": "#3498db",
    "swimming": "#1abc9c",
    "walking": "#95a5a6",
    "hiking": "#27ae60",
    "strength": "#8e44ad",
    "paddleboarding": "#e67e22",
    "skiing": "#2980b9",
    "unknown": "#bdc3c7",
}


def sport_color(sport: str) -> str:
    return SPORT_COLORS.get(sport, "#7f8c8d")


def weekly_volume(activities: list[dict], weeks: int = 52) -> dict:
    """Weekly distance and duration for the last N weeks."""
    from datetime import timedelta

    now = datetime.now()
    cutoff = now - timedelta(weeks=weeks)

    # Build weekly buckets
    buckets: dict[str, dict] = {}
    for a in activities:
        ts = a.get("start_time")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts).replace(tzinfo=None)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        # ISO week start (Monday)
        week_start = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        if week_start not in buckets:
            buckets[week_start] = {"distance": 0.0, "duration": 0.0, "count": 0}
        buckets[week_start]["distance"] += a.get("total_distance") or 0
        buckets[week_start]["duration"] += a.get("total_duration") or 0
        buckets[week_start]["count"] += 1

    sorted_weeks = sorted(buckets.keys())
    return {
        "weeks": sorted_weeks,
        "distance": [buckets[w]["distance"] for w in sorted_weeks],
        "duration": [buckets[w]["duration"] for w in sorted_weeks],
        "count": [buckets[w]["count"] for w in sorted_weeks],
    }


def monthly_volume(activities: list[dict]) -> dict:
    """Monthly distance and duration."""
    buckets: dict[str, dict] = {}
    for a in activities:
        ts = a.get("start_time")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts).replace(tzinfo=None)
        except ValueError:
            continue
        key = dt.strftime("%Y-%m")
        if key not in buckets:
            buckets[key] = {"distance": 0.0, "duration": 0.0, "count": 0}
        buckets[key]["distance"] += a.get("total_distance") or 0
        buckets[key]["duration"] += a.get("total_duration") or 0
        buckets[key]["count"] += 1

    sorted_months = sorted(buckets.keys())
    return {
        "months": sorted_months,
        "distance": [buckets[m]["distance"] for m in sorted_months],
        "duration": [buckets[m]["duration"] for m in sorted_months],
        "count": [buckets[m]["count"] for m in sorted_months],
    }


HR_ZONES = [
    ("Zone 1 (Recovery)", 0, 0.6),
    ("Zone 2 (Easy)", 0.6, 0.7),
    ("Zone 3 (Aerobic)", 0.7, 0.8),
    ("Zone 4 (Threshold)", 0.8, 0.9),
    ("Zone 5 (Max)", 0.9, 1.0),
]


def hr_zone_distribution(activities: list[dict], max_hr: int = 190) -> dict:
    """Estimate HR zone distribution from avg HR of each activity."""
    zone_minutes: dict[str, float] = {name: 0.0 for name, _, _ in HR_ZONES}

    for a in activities:
        hr = a.get("avg_heart_rate")
        dur = a.get("total_duration")
        if not hr or not dur:
            continue
        pct = hr / max_hr
        for name, lo, hi in HR_ZONES:
            if lo <= pct < hi or (hi == 1.0 and pct >= hi):
                zone_minutes[name] += dur / 60
                break

    return {
        "zones": [name for name, _, _ in HR_ZONES],
        "minutes": [zone_minutes[name] for name, _, _ in HR_ZONES],
    }


def pace_trends(activities: list[dict], sport: str = "running") -> dict:
    """Average pace over time for a given sport."""
    data = []
    for a in activities:
        if a.get("sport") != sport:
            continue
        ts = a.get("start_time")
        speed = a.get("avg_speed")
        if not ts or not speed or speed <= 0:
            continue
        pace_min_per_mi = (1609.344 / speed) / 60
        data.append({"date": ts[:10], "pace": pace_min_per_mi})

    data.sort(key=lambda x: x["date"])
    return {
        "dates": [d["date"] for d in data],
        "pace": [d["pace"] for d in data],
    }


STANDARD_DISTANCES = [
    ("1 mile", 1609.344),
    ("5K", 5000),
    ("10K", 10000),
    ("Half Marathon", 21097.5),
    ("Marathon", 42195),
]


def personal_records(activities: list[dict]) -> list[dict]:
    """Find fastest times for standard distances (running only)."""
    records = []
    for label, target_dist in STANDARD_DISTANCES:
        best_time = None
        best_date = None
        best_pace = None
        for a in activities:
            if a.get("sport") != "running":
                continue
            if a.get("has_anomalies"):
                continue
            dist = a.get("total_distance")
            dur = a.get("total_duration")
            if not dist or not dur or dist < target_dist * 0.95:
                continue
            # Estimate time for the target distance
            est_time = dur * (target_dist / dist)
            if best_time is None or est_time < best_time:
                best_time = est_time
                best_date = a.get("start_time", "")[:10]
                best_pace = (1609.344 / (target_dist / est_time)) / 60 if est_time > 0 else None
        records.append(
            {
                "distance": label,
                "time": best_time,
                "date": best_date,
                "pace": best_pace,
            }
        )
    return records


def streaks(activities: list[dict]) -> dict:
    """Compute current and longest activity streaks (consecutive days)."""
    dates: set[str] = set()
    for a in activities:
        ts = a.get("start_time")
        if ts:
            try:
                dates.add(datetime.fromisoformat(ts).strftime("%Y-%m-%d"))
            except ValueError:
                pass

    if not dates:
        return {"current": 0, "longest": 0}

    from datetime import timedelta

    sorted_dates = sorted(dates)
    longest = 1
    current_len = 1
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for i in range(1, len(sorted_dates)):
        prev = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            current_len += 1
            longest = max(longest, current_len)
        else:
            current_len = 1

    # Current streak: only counts if includes today or yesterday
    current = 0
    if sorted_dates[-1] in (today, yesterday):
        current = 1
        for i in range(len(sorted_dates) - 2, -1, -1):
            prev = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
            curr = datetime.strptime(sorted_dates[i + 1], "%Y-%m-%d")
            if (curr - prev).days == 1:
                current += 1
            else:
                break

    return {"current": current, "longest": longest}

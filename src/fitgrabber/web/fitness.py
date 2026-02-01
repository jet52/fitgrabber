"""Per-activity and aggregate fitness/efficiency metrics."""

from datetime import datetime


def activity_metrics(detail: dict) -> dict:
    """Compute training metrics for a single activity."""
    metrics: dict = {}
    points = detail.get("track_points", [])
    hr = detail.get("avg_heart_rate")
    dist = detail.get("total_distance")
    dur = detail.get("total_duration")

    if not points or len(points) < 10:
        return metrics

    # Cardiac cost: beats per mile
    if hr and dist and dist > 0 and dur and dur > 0:
        total_beats = hr * (dur / 60)
        miles = dist / 1609.344
        metrics["cardiac_cost"] = total_beats / miles if miles > 0 else None

    # Efficiency Factor: speed / HR
    if hr and hr > 0 and dist and dur and dur > 0:
        speed = dist / dur  # m/s
        metrics["efficiency_factor"] = speed / hr

    # Split-half analysis for decoupling and drift
    half = len(points) // 2
    first_half = points[:half]
    second_half = points[half:]

    fh_hr = _avg_field(first_half, "heart_rate")
    sh_hr = _avg_field(second_half, "heart_rate")
    fh_speed = _avg_field(first_half, "speed")
    sh_speed = _avg_field(second_half, "speed")
    fh_power = _avg_field(first_half, "power")
    sh_power = _avg_field(second_half, "power")

    # Aerobic decoupling (Pa:Hr)
    if fh_hr and sh_hr and fh_hr > 0 and sh_hr > 0 and fh_speed and sh_speed:
        ef_first = fh_speed / fh_hr
        ef_second = sh_speed / sh_hr
        if ef_first > 0:
            metrics["decoupling_pct"] = ((ef_first - ef_second) / ef_first) * 100

    # Cardiac drift: % HR increase from first to second half
    if fh_hr and sh_hr and fh_hr > 0:
        metrics["cardiac_drift_pct"] = ((sh_hr - fh_hr) / fh_hr) * 100

    # Pace drift: % pace slowdown from first to second half
    if fh_speed and sh_speed and fh_speed > 0:
        metrics["pace_drift_pct"] = ((fh_speed - sh_speed) / fh_speed) * 100

    # Power-based metrics (Stryd)
    avg_power = _avg_field(points, "power")
    avg_speed = _avg_field(points, "speed")

    if avg_power and avg_speed and avg_power > 0:
        metrics["running_effectiveness"] = avg_speed / avg_power

    if avg_power and hr and hr > 0:
        metrics["power_hr_ratio"] = avg_power / hr

    # Power decoupling
    if fh_hr and sh_hr and fh_hr > 0 and sh_hr > 0 and fh_power and sh_power:
        pef_first = fh_power / fh_hr
        pef_second = sh_power / sh_hr
        if pef_first > 0:
            metrics["power_decoupling_pct"] = ((pef_first - pef_second) / pef_first) * 100

    # TRIMP (training impulse)
    if hr and dur:
        max_hr = detail.get("max_heart_rate") or 190
        rest_hr = 60  # assumed resting HR
        hr_reserve_pct = (hr - rest_hr) / (max_hr - rest_hr) if max_hr > rest_hr else 0
        hr_reserve_pct = max(0, min(1, hr_reserve_pct))
        # Exponential weighting (male formula)
        trimp = (dur / 60) * hr_reserve_pct * 0.64 * (2.718 ** (1.92 * hr_reserve_pct))
        metrics["trimp"] = trimp

    return metrics


def aggregate_fitness(activities: list[dict]) -> dict:
    """Compute aggregate fitness trends from activity list.

    Only uses activities with HR data and sport=running.
    """
    running = [
        a
        for a in activities
        if a.get("sport") == "running"
        and a.get("avg_heart_rate")
        and a.get("total_distance")
        and a.get("total_duration")
        and not a.get("has_anomalies")
    ]
    running.sort(key=lambda a: a.get("start_time") or "")

    ef_trend: list[dict] = []
    cardiac_cost_trend: list[dict] = []

    for a in running:
        ts = a.get("start_time", "")[:10]
        hr = a["avg_heart_rate"]
        dist = a["total_distance"]
        dur = a["total_duration"]
        if not hr or not dist or not dur or hr <= 0 or dist <= 0 or dur <= 0:
            continue

        speed = dist / dur
        ef = speed / hr
        ef_trend.append({"date": ts, "value": ef})

        miles = dist / 1609.344
        beats = hr * (dur / 60)
        if miles > 0:
            cardiac_cost_trend.append({"date": ts, "value": beats / miles})

    return {
        "ef_trend": ef_trend,
        "cardiac_cost_trend": cardiac_cost_trend,
    }


def training_load(activities: list[dict]) -> dict:
    """Compute TRIMP-based training load metrics."""
    from datetime import timedelta

    # Compute daily TRIMP
    daily: dict[str, float] = {}
    for a in activities:
        if a.get("has_anomalies"):
            continue
        ts = a.get("start_time")
        hr = a.get("avg_heart_rate")
        dur = a.get("total_duration")
        if not ts or not hr or not dur:
            continue
        date = ts[:10]
        max_hr = a.get("max_heart_rate") or 190
        rest_hr = 60
        hr_reserve = (hr - rest_hr) / (max_hr - rest_hr) if max_hr > rest_hr else 0
        hr_reserve = max(0, min(1, hr_reserve))
        trimp = (dur / 60) * hr_reserve * 0.64 * (2.718 ** (1.92 * hr_reserve))
        daily[date] = daily.get(date, 0) + trimp

    if not daily:
        return {"dates": [], "daily_trimp": [], "atl": [], "ctl": [], "acr": []}

    # Fill in missing days with 0
    sorted_dates = sorted(daily.keys())
    start = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
    end = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
    all_dates = []
    all_trimp = []
    d = start
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        all_dates.append(ds)
        all_trimp.append(daily.get(ds, 0))
        d += timedelta(days=1)

    # Exponential moving averages
    atl = []  # Acute (7-day)
    ctl = []  # Chronic (42-day)
    acr = []  # Acute:Chronic ratio
    atl_val = 0.0
    ctl_val = 0.0
    for t in all_trimp:
        atl_val = atl_val + (t - atl_val) * (2 / (7 + 1))
        ctl_val = ctl_val + (t - ctl_val) * (2 / (42 + 1))
        atl.append(atl_val)
        ctl.append(ctl_val)
        acr.append(atl_val / ctl_val if ctl_val > 0 else 0)

    # Only return last 365 days
    n = min(len(all_dates), 365)
    return {
        "dates": all_dates[-n:],
        "daily_trimp": all_trimp[-n:],
        "atl": atl[-n:],
        "ctl": ctl[-n:],
        "acr": acr[-n:],
    }


def best_efforts(activities: list[dict]) -> dict:
    """Find best efforts at various durations across all activities.

    Note: requires track point data, so this works on detail-level data.
    For the aggregate view, we estimate from activity-level data.
    """
    # Estimate from activity summaries (best avg speed for activities >= duration)
    durations = [
        ("1 min", 60),
        ("5 min", 300),
        ("12 min", 720),
        ("20 min", 1200),
        ("60 min", 3600),
    ]

    results = []
    for label, target_dur in durations:
        best_speed = None
        best_date = None
        for a in activities:
            if a.get("sport") != "running" or a.get("has_anomalies"):
                continue
            dist = a.get("total_distance")
            dur = a.get("total_duration")
            if not dist or not dur or dur < target_dur * 0.9:
                continue
            speed = dist / dur
            if best_speed is None or speed > best_speed:
                best_speed = speed
                best_date = a.get("start_time", "")[:10]
        pace = None
        if best_speed and best_speed > 0:
            pace = (1609.344 / best_speed) / 60
        results.append({"duration": label, "speed": best_speed, "pace": pace, "date": best_date})

    return {"efforts": results}


def pace_distribution(activities: list[dict]) -> dict:
    """Aggregate time spent in pace zones across all running activities."""
    # Pace zones in min/mi
    zones = [
        ("< 7:00", 0, 7),
        ("7:00-8:00", 7, 8),
        ("8:00-9:00", 8, 9),
        ("9:00-10:00", 9, 10),
        ("10:00-11:00", 10, 11),
        ("11:00-12:00", 11, 12),
        ("> 12:00", 12, 99),
    ]
    zone_minutes = {label: 0.0 for label, _, _ in zones}

    for a in activities:
        if a.get("sport") != "running" or a.get("has_anomalies"):
            continue
        speed = a.get("avg_speed")
        dur = a.get("total_duration")
        if not speed or speed <= 0 or not dur:
            continue
        pace_min_mi = (1609.344 / speed) / 60
        for label, lo, hi in zones:
            if lo <= pace_min_mi < hi:
                zone_minutes[label] += dur / 60
                break

    return {
        "zones": [label for label, _, _ in zones],
        "minutes": [zone_minutes[label] for label, _, _ in zones],
    }


def _avg_field(points: list[dict], field: str) -> float | None:
    vals = [p[field] for p in points if p.get(field) is not None]
    return sum(vals) / len(vals) if vals else None

"""Flask app factory and routes for fitgrabber web UI."""

from datetime import datetime, timedelta

from flask import Flask, redirect, render_template, request, url_for

from fitgrabber.config import Config

from .services import (
    COVERAGE_FIELDS,
    delete_activity,
    flag_activity,
    get_activity_detail,
    get_comparison_data,
    get_dashboard_stats,
    get_merge_sources,
    get_processed_activities,
    invalidate_cache,
    unflag_activity,
)


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    app.config["cfg"] = cfg

    @app.template_filter("format_distance")
    def format_distance(meters: float | None) -> str:
        if not meters:
            return "-"
        miles = meters / 1609.344
        return f"{miles:.1f} mi"

    @app.template_filter("format_duration")
    def format_duration(seconds: float | None) -> str:
        if not seconds:
            return "-"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @app.template_filter("format_pace")
    def format_pace(speed_ms: float | None) -> str:
        if not speed_ms or speed_ms <= 0:
            return "-"
        pace_s_per_mi = 1609.344 / speed_ms
        m = int(pace_s_per_mi // 60)
        s = int(pace_s_per_mi % 60)
        return f"{m}:{s:02d} /mi"

    @app.template_filter("format_datetime")
    def format_datetime(ts: str | None) -> str:
        if not ts:
            return "-"
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(ts)
            return dt.strftime("%b %d, %Y %H:%M")
        except ValueError:
            return ts

    @app.template_filter("format_date")
    def format_date(ts: str | None) -> str:
        if not ts:
            return "-"
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(ts)
            return dt.strftime("%b %d, %Y")
        except ValueError:
            return ts

    @app.route("/")
    def dashboard():
        activities = get_processed_activities(cfg)
        clean = [a for a in activities if not a.get("has_anomalies")]
        stats = get_dashboard_stats(clean)
        return render_template("dashboard.html", stats=stats)

    ACTIVITY_COLUMNS = [
        ("start_time", "Date"),
        ("sport", "Sport"),
        ("name", "Name"),
        ("total_distance", "Distance"),
        ("total_duration", "Duration"),
        ("avg_heart_rate", "Avg HR"),
        ("source_platform", "Source"),
        ("has_anomalies", "Anomalies"),
    ]

    @app.route("/activities")
    def activities():
        from collections import defaultdict

        all_activities = get_processed_activities(cfg)
        sport_filter = request.args.get("sport", "")
        sub_sport_filter = request.args.get("sub_sport", "")
        anomalies_filter = request.args.get("anomalies", "")
        sort_by = request.args.get("sort", "start_time")
        sort_dir = request.args.get("dir", "desc")

        filtered = all_activities
        if sport_filter:
            filtered = [e for e in filtered if e.get("sport") == sport_filter]
            if sub_sport_filter:
                filtered = [e for e in filtered if e.get("sub_sport") == sub_sport_filter]
        if anomalies_filter == "yes":
            filtered = [e for e in filtered if e.get("has_anomalies")]
        elif anomalies_filter == "no":
            filtered = [e for e in filtered if not e.get("has_anomalies")]

        def sort_key(e: dict) -> object:
            v = e.get(sort_by)
            if v is None:
                return ""
            if isinstance(v, bool):
                return int(v)
            return v

        filtered = sorted(filtered, key=sort_key, reverse=(sort_dir == "desc"))

        # Build category list and subcategory map
        sports = sorted({e.get("sport", "unknown") for e in all_activities})
        sub_sports: dict[str, list[str]] = defaultdict(set)
        for e in all_activities:
            s = e.get("sub_sport")
            if s:
                sub_sports[e.get("sport", "unknown")].add(s)
        sub_sports_sorted = {k: sorted(v) for k, v in sub_sports.items()}

        # Helper to build query strings preserving current filters
        def filter_qs(**overrides: str) -> str:
            params = {
                "sport": sport_filter,
                "sub_sport": sub_sport_filter,
                "anomalies": anomalies_filter,
                "sort": sort_by,
                "dir": sort_dir,
            }
            params.update(overrides)
            return "&".join(f"{k}={v}" for k, v in params.items() if v)

        def sort_qs(col: str) -> str:
            new_dir = "asc" if sort_by == col and sort_dir == "desc" else "desc"
            return filter_qs(sort=col, dir=new_dir)

        return render_template(
            "activities.html",
            activities=filtered,
            sports=sports,
            sub_sports=sub_sports_sorted,
            columns=ACTIVITY_COLUMNS,
            current_sport=sport_filter,
            current_sub_sport=sub_sport_filter,
            current_anomalies=anomalies_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
            filter_qs=filter_qs,
            sort_qs=sort_qs,
        )

    @app.route("/activity/<activity_id>")
    def activity_detail_view(activity_id: str):
        from .fitness import activity_metrics

        detail = get_activity_detail(cfg, activity_id)
        if not detail:
            return render_template("404.html", message="Activity not found"), 404
        is_merged = "_merged" in str(detail.get("source_file", ""))
        training_metrics = activity_metrics(detail)
        # Check if flagged as anomaly
        from .services import _build_anomaly_prefixes

        anomaly_prefixes = _build_anomaly_prefixes(cfg)
        parts = activity_id.split("_", 2)
        prefix = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else activity_id
        is_flagged = prefix in anomaly_prefixes
        return render_template(
            "activity.html",
            activity=detail,
            activity_id=activity_id,
            is_merged=is_merged,
            training_metrics=training_metrics,
            is_flagged=is_flagged,
        )

    @app.route("/activity/<activity_id>/merge")
    def merge_view(activity_id: str):
        import json as json_mod

        detail = get_activity_detail(cfg, activity_id)
        if not detail:
            return render_template("404.html", message="Activity not found"), 404
        sources = get_merge_sources(cfg, activity_id)
        comparison = get_comparison_data(sources)
        # Strip track_points from sources_json to keep payload small for template
        sources_lite = [{k: v for k, v in s.items() if k != "track_points"} for s in sources]
        return render_template(
            "merge.html",
            activity=detail,
            activity_id=activity_id,
            sources=sources,
            sources_json=json_mod.dumps(sources_lite, default=str),
            coverage_fields=COVERAGE_FIELDS,
            comparison_data=comparison,
            comparison_json=json_mod.dumps(comparison, default=str) if comparison else "null",
        )

    @app.route("/calendar")
    def calendar():
        import calendar as cal_mod

        from .analytics import calendar_data, sport_color

        activities = get_processed_activities(cfg)
        year = request.args.get("year", type=int, default=datetime.now().year)
        month = request.args.get("month", type=int, default=datetime.now().month)
        cal = calendar_data(activities, year, month)
        month_name = cal_mod.month_name[month]
        prev_month = 12 if month == 1 else month - 1
        prev_year = year - 1 if month == 1 else year
        next_month = 1 if month == 12 else month + 1
        next_year = year + 1 if month == 12 else year
        return render_template(
            "calendar.html",
            cal=cal,
            month_name=month_name,
            prev_year=prev_year,
            prev_month=prev_month,
            next_year=next_year,
            next_month=next_month,
            sport_color=sport_color,
        )

    @app.route("/analytics")
    def analytics():
        import json as json_mod

        from .analytics import (
            hr_zone_distribution,
            monthly_volume,
            pace_trends,
            personal_records,
            streaks,
            weekly_volume,
        )
        from .fitness import (
            aggregate_fitness,
            best_efforts,
            pace_distribution,
            training_load,
        )

        all_activities = get_processed_activities(cfg)

        # Date range filtering
        range_param = request.args.get("range", "90")
        range_days = {"30": 30, "90": 90, "365": 365}.get(range_param)
        if range_days:
            cutoff = (datetime.now() - timedelta(days=range_days)).isoformat()
            filtered = [
                a for a in all_activities if a.get("start_time") and a["start_time"] >= cutoff
            ]
        else:
            filtered = all_activities

        clean = [a for a in filtered if not a.get("has_anomalies")]
        streak = streaks(filtered)
        weekly = weekly_volume(clean)
        monthly = monthly_volume(clean)
        hr_zones = hr_zone_distribution(clean)
        pace = pace_trends(clean)
        prs = personal_records(clean)
        sports = {a.get("sport", "unknown") for a in filtered}
        fitness = aggregate_fitness(clean)
        load = training_load(clean)
        efforts = best_efforts(clean)
        pace_dist = pace_distribution(clean)
        return render_template(
            "analytics.html",
            streak=streak,
            total_count=len(filtered),
            sport_count=len(sports),
            prs=prs,
            weekly_json=json_mod.dumps(weekly),
            monthly_json=json_mod.dumps(monthly),
            hr_zones_json=json_mod.dumps(hr_zones),
            pace_trend_json=json_mod.dumps(pace),
            fitness_json=json_mod.dumps(fitness),
            load_json=json_mod.dumps(load),
            efforts=efforts.get("efforts", []),
            pace_dist_json=json_mod.dumps(pace_dist),
            current_range=range_param,
        )

    @app.route("/api/activity/<activity_id>/flag", methods=["POST"])
    def flag_activity_view(activity_id: str):
        flag_activity(cfg, activity_id)
        return redirect(url_for("activity_detail_view", activity_id=activity_id))

    @app.route("/api/activity/<activity_id>/unflag", methods=["POST"])
    def unflag_activity_view(activity_id: str):
        unflag_activity(cfg, activity_id)
        return redirect(url_for("activity_detail_view", activity_id=activity_id))

    @app.route("/api/activity/<activity_id>/delete", methods=["POST"])
    def delete_activity_view(activity_id: str):
        delete_activity(cfg, activity_id)
        return redirect(url_for("activities"))

    @app.route("/api/refresh", methods=["POST"])
    def refresh():
        invalidate_cache()
        return {"status": "ok"}

    return app

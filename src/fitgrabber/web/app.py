"""Flask app factory and routes for fitgrabber web UI."""

from datetime import datetime

from flask import Flask, render_template, request

from fitgrabber.config import Config

from .services import (
    COVERAGE_FIELDS,
    get_activity_detail,
    get_comparison_data,
    get_dashboard_stats,
    get_merge_sources,
    get_processed_activities,
    invalidate_cache,
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
        stats = get_dashboard_stats(activities)
        return render_template("dashboard.html", stats=stats)

    @app.route("/activities")
    def activities():
        all_activities = get_processed_activities(cfg)
        sport_filter = request.args.get("sport", "")
        sort_by = request.args.get("sort", "start_time")
        sort_dir = request.args.get("dir", "desc")

        filtered = all_activities
        if sport_filter:
            filtered = [e for e in filtered if e.get("sport") == sport_filter]

        reverse = sort_dir == "desc"
        filtered = sorted(filtered, key=lambda e: e.get(sort_by) or "", reverse=reverse)

        sports = sorted({e.get("sport", "unknown") for e in all_activities})
        return render_template(
            "activities.html",
            activities=filtered,
            sports=sports,
            current_sport=sport_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    @app.route("/activity/<activity_id>")
    def activity_detail_view(activity_id: str):
        detail = get_activity_detail(cfg, activity_id)
        if not detail:
            return render_template("404.html", message="Activity not found"), 404
        is_merged = "_merged" in str(detail.get("source_file", ""))
        return render_template(
            "activity.html", activity=detail, activity_id=activity_id, is_merged=is_merged
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

        activities = get_processed_activities(cfg)
        streak = streaks(activities)
        weekly = weekly_volume(activities)
        monthly = monthly_volume(activities)
        hr_zones = hr_zone_distribution(activities)
        pace = pace_trends(activities)
        prs = personal_records(activities)
        sports = {a.get("sport", "unknown") for a in activities}
        return render_template(
            "analytics.html",
            streak=streak,
            total_count=len(activities),
            sport_count=len(sports),
            prs=prs,
            weekly_json=json_mod.dumps(weekly),
            monthly_json=json_mod.dumps(monthly),
            hr_zones_json=json_mod.dumps(hr_zones),
            pace_trend_json=json_mod.dumps(pace),
        )

    @app.route("/api/refresh", methods=["POST"])
    def refresh():
        invalidate_cache()
        return {"status": "ok"}

    return app

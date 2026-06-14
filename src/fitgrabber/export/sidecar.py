import json
from pathlib import Path

from fitgrabber.parsers.models import Activity


def sidecar_path(fit_path: Path) -> Path:
    """Companion metadata path for a merged FIT (e.g. ..._merged.meta.json)."""
    return fit_path.with_name(fit_path.stem + ".meta.json")


def write_sidecar(activity: Activity, fit_path: Path) -> Path:
    """Write provenance + retained R-R data alongside a merged FIT file."""
    path = sidecar_path(fit_path)
    data = {
        "power_source": activity.power_source,
        "power_source_alt": activity.power_source_alt,
        "hr_source": activity.hr_source,
        "hr_detail": activity.hr_detail,
        "has_rr": bool(activity.rr_intervals),
        "sources": activity.metadata.get("sources", []),
    }
    if activity.rr_intervals:
        data["rr_ms"] = [round(v * 1000) for v in activity.rr_intervals]
    path.write_text(json.dumps(data, indent=2, default=str))
    return path

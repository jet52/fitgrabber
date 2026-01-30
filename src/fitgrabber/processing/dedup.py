from datetime import datetime, timedelta

START_TIME_TOLERANCE = timedelta(minutes=5)


def find_duplicates(catalog: list[dict]) -> list[list[dict]]:
    """Find groups of catalog entries that are likely the same activity.

    Groups by overlapping time windows regardless of sport type, since
    activities may have incorrect types attached.
    """
    groups: list[list[dict]] = []
    used: set[str] = set()

    entries = [e for e in catalog if e.get("start_time")]
    entries.sort(key=lambda e: e["start_time"])

    for i, a in enumerate(entries):
        if a["source_file"] in used:
            continue
        group = [a]
        used.add(a["source_file"])

        for b in entries[i + 1 :]:
            if b["source_file"] in used:
                continue
            if _overlaps(a, b):
                group.append(b)
                used.add(b["source_file"])

        if len(group) > 1:
            groups.append(group)

    return groups


def _overlaps(a: dict, b: dict) -> bool:
    """Two entries overlap if their time windows are within tolerance."""
    ta = datetime.fromisoformat(a["start_time"])
    tb = datetime.fromisoformat(b["start_time"])
    return abs(ta - tb) <= START_TIME_TOLERANCE

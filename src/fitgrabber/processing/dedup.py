from datetime import timedelta

START_TIME_TOLERANCE = timedelta(minutes=5)


def find_duplicates(catalog: list[dict]) -> list[list[dict]]:
    """Find groups of catalog entries that are likely the same activity."""
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
            if _is_duplicate(a, b):
                group.append(b)
                used.add(b["source_file"])

        if len(group) > 1:
            groups.append(group)

    return groups


def _is_duplicate(a: dict, b: dict) -> bool:
    """Two entries are duplicates if they have similar start times and sport."""
    from datetime import datetime

    ta = datetime.fromisoformat(a["start_time"])
    tb = datetime.fromisoformat(b["start_time"])
    if abs(ta - tb) > START_TIME_TOLERANCE:
        return False
    if a.get("sport") and b.get("sport") and a["sport"] != b["sport"]:
        return False
    return True

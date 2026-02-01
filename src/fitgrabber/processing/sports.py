"""Shared sport taxonomy: raw sport strings → (category, subcategory)."""

SPORT_TAXONOMY: dict[str, tuple[str, str | None]] = {
    # Running
    "running": ("running", None),
    "run": ("running", None),
    "trailrun": ("running", "trail"),
    "trail_run": ("running", "trail"),
    "trail running": ("running", "trail"),
    "virtualrun": ("running", "virtual"),
    "treadmill": ("running", "treadmill"),
    "treadmill running": ("running", "treadmill"),
    # Cycling
    "cycling": ("cycling", None),
    "ride": ("cycling", None),
    "virtualride": ("cycling", "virtual"),
    "mountainbikeride": ("cycling", "mountain bike"),
    "mountain biking": ("cycling", "mountain bike"),
    "ebikeride": ("cycling", "e-bike"),
    "gravelride": ("cycling", "gravel"),
    "gravel cycling": ("cycling", "gravel"),
    # Walking
    "walking": ("walking", None),
    "walk": ("walking", None),
    # Hiking
    "hiking": ("hiking", None),
    "hike": ("hiking", None),
    # Swimming
    "swimming": ("swimming", None),
    "swim": ("swimming", None),
    # Strength
    "strength": ("strength", None),
    "weighttraining": ("strength", "weights"),
    "training": ("strength", None),
    "workout": ("strength", None),
    # Skiing
    "skiing": ("skiing", None),
    "backcountryski": ("skiing", "backcountry"),
    "nordicski": ("skiing", "nordic"),
    "alpineski": ("skiing", "alpine"),
    # Paddleboarding
    "paddleboarding": ("paddleboarding", None),
    "standuppaddling": ("paddleboarding", None),
    "stand_up_paddleboarding": ("paddleboarding", None),
    # Other
    "generic": ("other", None),
}


def normalize_sport(raw: str) -> tuple[str, str | None]:
    """Return (category, subcategory) for a raw sport string."""
    key = raw.strip().lower()
    if key in SPORT_TAXONOMY:
        return SPORT_TAXONOMY[key]
    return (key, None)


def sport_category(raw: str) -> str:
    return normalize_sport(raw)[0]

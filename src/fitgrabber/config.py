from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

CONFIG_DIR = Path.home() / ".config" / "fitgrabber"
CONFIG_FILE = CONFIG_DIR / "config.toml"

PLATFORMS = [
    "garmin",
    "strava",
    "coros",
    "suunto",
    "stryd",
    "myfitnesspal",
    "sporttracks",
    "manual",
]

RAW_SUBDIRS = [f"raw/{p}" for p in PLATFORMS]
PROCESSED_SUBDIRS = ["processed/individual", "processed/merged"]


@dataclass
class Config:
    data_dir: Path = Path.home() / "FitnessData"
    platforms: dict[str, dict[str, Any]] = field(default_factory=dict)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"data_dir": str(self.data_dir), "platforms": self.platforms}
        CONFIG_FILE.write_bytes(tomli_w.dumps(data).encode())

    def init_data_dir(self) -> None:
        for sub in RAW_SUBDIRS + PROCESSED_SUBDIRS:
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    def raw_dir(self, platform: str) -> Path:
        return self.data_dir / "raw" / platform

    def processed_individual_dir(self) -> Path:
        return self.data_dir / "processed" / "individual"

    def processed_merged_dir(self) -> Path:
        return self.data_dir / "processed" / "merged"

    def catalog_path(self) -> Path:
        return self.data_dir / "processed" / "catalog.json"


def load_config() -> Config:
    if not CONFIG_FILE.exists():
        return Config()
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads(CONFIG_FILE.read_text())
    return Config(
        data_dir=Path(data.get("data_dir", str(Path.home() / "FitnessData"))),
        platforms=data.get("platforms", {}),
    )

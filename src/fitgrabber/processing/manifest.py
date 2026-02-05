"""Track which source files produced each processed output."""

import json
from pathlib import Path

from fitgrabber.config import Config


def manifest_path(cfg: Config) -> Path:
    return cfg.data_dir / "processed" / "manifest.json"


def load_manifest(cfg: Config) -> dict[str, dict]:
    """Load the processing manifest.

    Returns dict mapping ts_prefix -> {output_file, source_files}
    """
    path = manifest_path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_manifest(cfg: Config, manifest: dict[str, dict]) -> None:
    """Save the processing manifest."""
    path = manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

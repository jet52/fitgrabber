import shutil
from pathlib import Path

import typer

from fitgrabber.config import Config

SUPPORTED_EXTENSIONS = {".fit", ".gpx", ".tcx", ".csv"}


def sync(cfg: Config, platform: str, source_dir: Path | None = None) -> list[Path]:
    """Import files from a local directory into raw/<platform>/."""
    dest = cfg.raw_dir(platform)
    dest.mkdir(parents=True, exist_ok=True)

    if source_dir is None:
        source_cfg = cfg.platforms.get(platform, {})
        source_path = source_cfg.get("import_dir")
        if not source_path:
            typer.echo(f"  No import_dir configured for {platform}.")
            return []
        source_dir = Path(source_path)

    if not source_dir.exists():
        typer.echo(f"  Source directory not found: {source_dir}")
        return []

    all_files = [f for f in source_dir.rglob("*") if f.suffix.lower() in SUPPORTED_EXTENSIONS]
    typer.echo(f"  Found {len(all_files)} files in {source_dir}")

    imported: list[Path] = []
    skipped = 0

    for i, f in enumerate(all_files, 1):
        target = dest / f.name
        if target.exists():
            skipped += 1
            continue
        typer.echo(f"  [{i}/{len(all_files)}] Copying {f.name}")
        shutil.copy2(f, target)
        imported.append(target)

    if skipped:
        typer.echo(f"  Skipped {skipped} already imported.")
    return imported

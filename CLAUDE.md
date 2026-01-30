# fitgrabber

Python CLI tool for collecting, organizing, and analyzing personal fitness data.

## Data Sources

Garmin, COROS, Strava, Suunto, Stryd, MyFitnessPal, SportTracks.mobi

- Use official APIs with OAuth/tokens where available
- Fall back to file import where no API exists

## Supported Formats

FIT, GPX, TCX, CSV — all activity types (running, cycling, swimming, strength, etc.)

## Architecture

- **Code** lives in this project directory
- **Data** lives in a separate configurable directory (not in the repo)
  - `raw/` — downloaded data organized by source platform, never modified after download
  - `processed/individual/` — cleaned single-source activity files
  - `processed/merged/` — combined multi-source activity files (deduped, merged, anomalies flagged)

## Data Processing Pipeline

1. Download/sync from each platform
2. Parse all formats into a common internal representation
3. Detect duplicates (same activity on multiple devices/platforms)
4. Merge overlapping activities: union of time ranges, union of data fields
5. Detect and flag spurious data (GPS anomalies, forgot-to-stop-watch segments)
6. Output complete fitness history as clean files

## Design Principles

- Prefer free/open-source libraries; write new code when simpler than adding a dependency
- Raw data is immutable — all processing produces new files
- Configurable data directory path (no hardcoded paths)

## Tooling

- Package management: uv
- Linting/formatting: ruff
- `uv run fitgrabber` — run the CLI
- `uv run ruff check .` — lint
- `uv run ruff format .` — format
- `uv run pytest` — test

## Style Rules

- Concise, minimal code — short functions, pragmatic approach
- Type hints throughout
- Minimal comments — only where logic isn't self-evident
- No excessive docstrings

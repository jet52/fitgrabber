# fitgrabber

CLI tool for collecting, organizing, and analyzing personal fitness data from multiple platforms.

## Supported Platforms

| Platform | Method | Data |
|----------|--------|------|
| Garmin Connect | API (email/password) | Activities as FIT/TCX/GPX |
| Strava | API (OAuth) | Activities as JSON with streams |
| MyFitnessPal | Scraping (username) | Nutrition data as CSV |
| COROS | Manual file import | FIT exports |
| Suunto | Manual file import | FIT/GPX exports |
| Stryd | Manual file import | FIT exports |
| SportTracks | Manual file import | CSV/FIT exports |

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Configuration

Config lives at `~/.config/fitgrabber/config.toml`. Create it manually or run:

```bash
uv run fitgrabber config --data-dir /path/to/your/data
```

### Sample config

```toml
data_dir = "/path/to/your/fitness-data"

[platforms.garmin]
email = "you@example.com"
password = "your-password"

[platforms.strava]
client_id = ""
client_secret = ""
access_token = ""
refresh_token = ""
expires_at = 0

[platforms.myfitnesspal]
username = "your-username"
days = 365

[platforms.coros]
import_dir = "/path/to/coros/exports"

[platforms.suunto]
import_dir = "/path/to/suunto/exports"

[platforms.stryd]
import_dir = "/path/to/stryd/exports"

[platforms.sporttracks]
import_dir = "/path/to/sporttracks/exports"

[platforms.manual]
import_dir = ""
```

### Garmin setup

Add your Garmin Connect email and password to the config. That's it.

### Strava setup

Strava requires OAuth. One-time setup:

1. Go to https://www.strava.com/settings/api and create an app:
   - **Application Name**: fitgrabber
   - **Category**: Data Importer
   - **Website**: http://localhost
   - **Authorization Callback Domain**: localhost

2. Note your **Client ID** and **Client Secret**.

3. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
   ```
   https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=read_all,activity:read_all&approval_prompt=force
   ```

4. Click Authorize. You'll be redirected to a localhost URL that won't load. Copy the `code` parameter from the URL bar.

5. Exchange the code for tokens:
   ```bash
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=CODE_FROM_STEP_4 \
     -d grant_type=authorization_code
   ```

6. Put `client_id`, `client_secret`, `access_token`, `refresh_token`, and `expires_at` from the JSON response into your config. Tokens will auto-refresh on future runs.

### Manual import platforms (COROS, Suunto, Stryd, SportTracks)

Export files from the platform's web app, put them in a directory, and set `import_dir` in the config. Supported formats: `.fit`, `.gpx`, `.tcx`, `.csv`.

## Usage

```bash
# Show help
uv run fitgrabber --help

# Set up data directory
uv run fitgrabber config --data-dir /path/to/data

# Show current config
uv run fitgrabber config --show

# Sync from one platform
uv run fitgrabber sync garmin
uv run fitgrabber sync strava

# Sync from all configured platforms
uv run fitgrabber sync all

# Check what's been downloaded
uv run fitgrabber status
```

## Data Directory Structure

```
<data_dir>/
  raw/
    garmin/          # FIT zips, TCX, GPX files
    strava/          # JSON files with metadata + streams
    coros/           # Imported FIT files
    suunto/          # Imported FIT/GPX files
    stryd/           # Imported FIT files
    myfitnesspal/    # Daily nutrition CSVs
    sporttracks/     # Imported files
    manual/          # Manually imported files
  processed/
    individual/      # Cleaned single-source activity files
    merged/          # Combined multi-source activity files
```

Raw data is never modified after download. All processing produces new files in `processed/`.

## Rate Limits and Retries

- **Garmin**: 1s delay between downloads, retries with backoff on server errors, falls back from FIT → TCX → GPX for older activities. Failed activity IDs saved to `.failed_activities.json` (delete to retry).
- **Strava**: 3s delay between activities, 15-minute pause on rate limit (100 req/15min, 1000/day), 30s request timeout.
- Both support Ctrl+C — progress is saved and re-runs skip already-downloaded files.

## Development

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run pytest          # test
```

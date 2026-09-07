# Nearest METAR

A small Flask app that uses browser geolocation to find and display the closest current METAR observation from NOAA's Aviation Weather Center.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5050> and allow location access. Browser geolocation works on `localhost`; a deployed copy must use HTTPS.

Port `5050` is used by default. If it is occupied, the app exits with a clear error instead of starting on a conflicting port. Choose another port with the `NEAREST_METAR_PORT` environment variable:

```bash
NEAREST_METAR_PORT=5051 python app.py
```

## Environment variables

All app-owned environment variables use the `NEAREST_METAR_` prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEAREST_METAR_HOST` | `127.0.0.1` | Address on which the Flask development server listens. Use `0.0.0.0` to make it reachable from other devices on the network. |
| `NEAREST_METAR_PORT` | `5050` | Port used by the Flask development server. |
| `NEAREST_METAR_CACHE_DIR` | OS-specific user cache directory | Directory containing the persisted NOAA station catalog. |
| `NEAREST_METAR_STATION_COUNT` | `7` | Number of nearest reporting stations returned for the selector. Must be a positive integer. |

For example:

```bash
NEAREST_METAR_HOST=0.0.0.0 NEAREST_METAR_PORT=5051 NEAREST_METAR_STATION_COUNT=10 python app.py
```

Unless `NEAREST_METAR_CACHE_DIR` is set, the cache location is selected according to the operating system:

| Operating system | Default location |
| --- | --- |
| macOS | `~/Library/Caches/nearest-metar` |
| Linux and other Unix systems | `$XDG_CACHE_HOME/nearest-metar`, or `~/.cache/nearest-metar` when `XDG_CACHE_HOME` is unset |
| Windows | `%LOCALAPPDATA%\NearestMETAR\Cache` |

The app downloads the official compressed current-METAR feed on demand and keeps it in memory for five minutes. NOAA's station catalog is persisted as `stations.json` in the cache directory and refreshed once per day. No API key is required.

Browser coordinates are sent to OpenStreetMap's Nominatim service to obtain a readable city/country label. The app does not persist those coordinates.

## Logging

After downloading a fresh observation feed, the app writes an info-level message with the number of METAR observations received:

```text
Fetched 5113 METAR observations from Aviation Weather Center
```

The feed is cached in memory for five minutes, so requests served from that cache do not produce another fetch message.

The included development server runs in debug mode and displays these messages when started with:

```bash
python app.py
```

METARs are aviation observations and this app is for informational use, not flight planning.

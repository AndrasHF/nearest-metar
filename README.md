# Nearest METAR

A small Flask app that uses browser geolocation to find and display the closest current METAR observation from NOAA's Aviation Weather Center.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run
```

Open <http://127.0.0.1:5000> and allow location access. Browser geolocation works on `localhost`; a deployed copy must use HTTPS.

The app downloads the official compressed current-METAR feed on demand and keeps it in memory for five minutes. NOAA's station catalog is persisted in `.cache/stations.json` and refreshed once per day. No API key is required.

Browser coordinates are sent to OpenStreetMap's Nominatim service to obtain a readable city/country label. The app does not persist those coordinates.

METARs are aviation observations and this app is for informational use, not flight planning.

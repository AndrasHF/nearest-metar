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

## Logging

After downloading a fresh observation feed, the app writes an info-level message with the number of METAR observations received:

```text
Fetched 5113 METAR observations from Aviation Weather Center
```

The feed is cached in memory for five minutes, so requests served from that cache do not produce another fetch message.

Flask normally hides info-level messages. Run the development server in debug mode to display them:

```bash
flask --app app run --debug
```

METARs are aviation observations and this app is for informational use, not flight planning.

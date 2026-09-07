from __future__ import annotations

import csv
import gzip
import heapq
import io
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, jsonify, render_template, request


METAR_CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
STATION_CACHE_URL = "https://aviationweather.gov/data/cache/stations.cache.json.gz"
REVERSE_GEOCODE_URL = "https://nominatim.openstreetmap.org/reverse"
CACHE_SECONDS = 300
STATION_CACHE_SECONDS = 86_400
NEAREST_STATION_COUNT = 7
USER_AGENT = "NearestMETAR/1.0 (personal weather display)"
CACHE_DIRECTORY = Path(os.getenv("WEATHER_CACHE_DIR", Path(__file__).parent / ".cache"))
STATION_CACHE_FILE = CACHE_DIRECTORY / "stations.json"

app = Flask(__name__)


@dataclass
class MetarCache:
    observations: list[dict]
    fetched_at: float


_cache: MetarCache | None = None
_cache_lock = threading.Lock()
_stations: dict[str, dict] | None = None
_stations_lock = threading.Lock()


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", "M") else None
    except (TypeError, ValueError):
        return None


def _first(row: dict, *names: str):
    for name in names:
        if row.get(name) not in (None, "", "M"):
            return row[name]
    return None


def parse_metar_csv(payload: bytes) -> list[dict]:
    """Parse AWC's gzip CSV cache, tolerating its explanatory preamble."""
    text = gzip.decompress(payload).decode("utf-8", errors="replace")
    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("raw_text,station_id,")),
        None,
    )
    if header_index is None:
        raise ValueError("The weather service returned an unexpected data format.")

    observations = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        lat = _number(row.get("latitude"))
        lon = _number(row.get("longitude"))
        if lat is not None and lon is not None and row.get("station_id"):
            row["_lat"] = lat
            row["_lon"] = lon
            observations.append(row)
    return observations


def fetch_observations() -> list[dict]:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache.fetched_at < CACHE_SECONDS:
        return _cache.observations

    with _cache_lock:
        now = time.monotonic()
        if _cache and now - _cache.fetched_at < CACHE_SECONDS:
            return _cache.observations
        req = urllib.request.Request(METAR_CACHE_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            observations = parse_metar_csv(response.read())
        if not observations:
            raise ValueError("No current weather observations were available.")
        app.logger.info("Fetched %d METAR observations from Aviation Weather Center", len(observations))
        _cache = MetarCache(observations, now)
        return observations


def _read_station_cache() -> dict[str, dict]:
    with STATION_CACHE_FILE.open(encoding="utf-8") as cache_file:
        return json.load(cache_file)


def _write_station_cache(stations: dict[str, dict]) -> None:
    CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = STATION_CACHE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as cache_file:
        json.dump(stations, cache_file, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, STATION_CACHE_FILE)


def fetch_station_metadata() -> dict[str, dict]:
    """Return station details, persisting the once-daily AWC catalog on disk."""
    global _stations
    if _stations is not None:
        return _stations

    with _stations_lock:
        if _stations is not None:
            return _stations
        cache_is_fresh = (
            STATION_CACHE_FILE.exists()
            and time.time() - STATION_CACHE_FILE.stat().st_mtime < STATION_CACHE_SECONDS
        )
        if cache_is_fresh:
            _stations = _read_station_cache()
            return _stations

        try:
            req = urllib.request.Request(STATION_CACHE_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as response:
                records = json.loads(gzip.decompress(response.read()).decode("utf-8"))
            _stations = {
                station_id: {
                    "name": record.get("site"),
                    "state": record.get("state"),
                    "country": record.get("country"),
                    "iata": record.get("iataId"),
                    "elevation_m": record.get("elev"),
                }
                for record in records
                if (station_id := record.get("icaoId") or record.get("id"))
            }
            _write_station_cache(_stations)
        except Exception:
            if not STATION_CACHE_FILE.exists():
                raise
            app.logger.warning("Using stale station metadata cache", exc_info=True)
            _stations = _read_station_cache()
        return _stations


def reverse_geocode(lat: float, lon: float, language: str = "en") -> dict:
    params = urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10, "addressdetails": 1}
    )
    headers = {"User-Agent": USER_AGENT, "Accept-Language": language[:64] or "en"}
    req = urllib.request.Request(f"{REVERSE_GEOCODE_URL}?{params}", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    address = result.get("address", {})
    locality = next(
        (address.get(key) for key in ("city", "town", "village", "municipality", "county") if address.get(key)),
        None,
    )
    country = address.get("country")
    label = ", ".join(part for part in (locality, country) if part)
    return {"name": label or result.get("display_name") or f"{lat:.4f}, {lon:.4f}", "latitude": lat, "longitude": lon}


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance using the haversine formula."""
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def present(row: dict, distance: float, metadata: dict | None = None) -> dict:
    temp = _number(_first(row, "temp_c", "temp"))
    dewpoint = _number(_first(row, "dewpoint_c", "dewp"))
    wind_speed = _number(_first(row, "wind_speed_kt", "wspd"))
    gust = _number(_first(row, "wind_gust_kt", "wgst"))
    visibility = _number(_first(row, "visibility_statute_mi", "visib"))
    altimeter = _number(_first(row, "altim_in_hg", "altim"))
    metadata = metadata or {}
    return {
        "station": row.get("station_id"),
        "distance_km": round(distance, 1),
        "latitude": row["_lat"],
        "longitude": row["_lon"],
        "observed_at": _first(row, "observation_time", "reportTime"),
        "raw": _first(row, "raw_text", "rawOb"),
        "flight_category": _first(row, "flight_category", "fltCat"),
        "temperature_c": temp,
        "dewpoint_c": dewpoint,
        "wind_direction": _first(row, "wind_dir_degrees", "wdir"),
        "wind_speed_kt": wind_speed,
        "wind_gust_kt": gust,
        "visibility_mi": visibility,
        "altimeter_in_hg": altimeter,
        "station_name": metadata.get("name"),
        "station_state": metadata.get("state"),
        "station_country": metadata.get("country"),
        "station_iata": metadata.get("iata"),
        "station_elevation_m": metadata.get("elevation_m"),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/metar")
def nearest_metar():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Please provide valid latitude and longitude values."), 400

    try:
        observations = fetch_observations()
        metadata = fetch_station_metadata()
        nearest = heapq.nsmallest(
            NEAREST_STATION_COUNT,
            observations,
            key=lambda item: distance_km(lat, lon, item["_lat"], item["_lon"]),
        )
        stations = [
            present(
                row,
                distance_km(lat, lon, row["_lat"], row["_lon"]),
                metadata.get(row.get("station_id")),
            )
            for row in nearest
        ]
        try:
            browser_location = reverse_geocode(lat, lon, request.args.get("lang", "en"))
        except Exception:
            app.logger.warning("Reverse geocoding unavailable", exc_info=True)
            browser_location = {"name": f"{lat:.4f}, {lon:.4f}", "latitude": lat, "longitude": lon}
        return jsonify(stations=stations, browser_location=browser_location)
    except (urllib.error.URLError, TimeoutError) as exc:
        app.logger.warning("Weather service unavailable: %s", exc)
        return jsonify(error="The aviation weather service is temporarily unavailable. Please try again."), 502
    except Exception as exc:
        app.logger.exception("Unable to load METAR data: %s", exc)
        return jsonify(error="Weather data could not be loaded right now."), 502


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=True)

import csv
import gzip
import io
import socket
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import app as weather_app


def make_feed(rows):
    output = io.StringIO()
    fields = ["raw_text", "station_id", "observation_time", "latitude", "longitude", "temp_c"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return gzip.compress(("No errors\nNo warnings\n3 results\n" + output.getvalue()).encode())


class WeatherAppTests(unittest.TestCase):
    def test_default_cache_directory_uses_os_convention(self):
        cache_directory = weather_app.default_cache_directory()
        if sys.platform == "darwin":
            self.assertEqual(cache_directory, Path.home() / "Library" / "Caches" / "nearest-metar")
        elif sys.platform == "win32":
            self.assertEqual(cache_directory.name, "Cache")
            self.assertEqual(cache_directory.parent.name, "NearestMETAR")
        else:
            self.assertEqual(cache_directory.name, "nearest-metar")

    def test_positive_integer_setting(self):
        with patch.dict("os.environ", {"TEST_NUMBER": "12"}):
            self.assertEqual(weather_app.positive_int_setting("TEST_NUMBER", 7), 12)

        for invalid in ("0", "-1", "many"):
            with self.subTest(value=invalid), patch.dict("os.environ", {"TEST_NUMBER": invalid}):
                with self.assertRaisesRegex(ValueError, "must be a positive integer"):
                    weather_app.positive_int_setting("TEST_NUMBER", 7)

    def test_parse_and_nearest_endpoint(self):
        rows = [
            {"raw_text": "METAR LHBP TEST", "station_id": "LHBP", "observation_time": "2026-01-01T12:00:00Z", "latitude": "47.44", "longitude": "19.26", "temp_c": "12"},
            {"raw_text": "METAR LOWW TEST", "station_id": "LOWW", "observation_time": "2026-01-01T12:00:00Z", "latitude": "48.11", "longitude": "16.57", "temp_c": "10"},
            {"raw_text": "METAR LZIB TEST", "station_id": "LZIB", "observation_time": "2026-01-01T12:00:00Z", "latitude": "48.17", "longitude": "17.21", "temp_c": "11"},
            {"raw_text": "METAR LHDC TEST", "station_id": "LHDC", "observation_time": "2026-01-01T12:00:00Z", "latitude": "47.49", "longitude": "21.61", "temp_c": "12"},
            {"raw_text": "METAR LHSM TEST", "station_id": "LHSM", "observation_time": "2026-01-01T12:00:00Z", "latitude": "46.69", "longitude": "17.16", "temp_c": "13"},
            {"raw_text": "METAR LHPP TEST", "station_id": "LHPP", "observation_time": "2026-01-01T12:00:00Z", "latitude": "45.99", "longitude": "18.24", "temp_c": "14"},
            {"raw_text": "METAR LKPR TEST", "station_id": "LKPR", "observation_time": "2026-01-01T12:00:00Z", "latitude": "50.10", "longitude": "14.26", "temp_c": "9"},
        ]
        observations = weather_app.parse_metar_csv(make_feed(rows))
        metadata = {
            "LHBP": {"name": "Budapest/Ferihegy", "state": "PE", "country": "HU", "iata": "BUD", "elevation_m": 151}
        }
        location = {"name": "Budapest, Hungary", "latitude": 47.5, "longitude": 19.1}
        with (
            patch.object(weather_app, "fetch_observations", return_value=observations),
            patch.object(weather_app, "fetch_station_metadata", return_value=metadata),
            patch.object(weather_app, "reverse_geocode", return_value=location),
        ):
            response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
        self.assertEqual(response.status_code, 200)
        stations = response.get_json()["stations"]
        self.assertEqual(len(stations), weather_app.NEAREST_STATION_COUNT)
        self.assertEqual(stations[0]["station"], "LHBP")
        self.assertEqual(stations[0]["station_name"], "Budapest/Ferihegy")
        self.assertEqual(response.get_json()["browser_location"]["name"], "Budapest, Hungary")
        self.assertEqual(
            [station["distance_km"] for station in stations],
            sorted(station["distance_km"] for station in stations),
        )

    def test_rejects_invalid_coordinates(self):
        response = weather_app.app.test_client().get("/api/metar?lat=999&lon=nope")
        self.assertEqual(response.status_code, 400)

    def test_haversine_known_distance(self):
        distance = weather_app.distance_km(47.5, 19.1, 48.1, 16.6)
        self.assertTrue(190 < distance < 210)

    def test_port_conflict_exits_with_clear_message(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("0.0.0.0", 0))
        port = listener.getsockname()[1]
        try:
            with self.assertRaises(SystemExit) as raised:
                weather_app.ensure_port_available(port)
        finally:
            listener.close()
        self.assertIn(f"port {port} is already in use", str(raised.exception))

    def test_available_port_passes_check(self):
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        candidate.bind(("0.0.0.0", 0))
        port = candidate.getsockname()[1]
        candidate.close()
        weather_app.ensure_port_available(port)


if __name__ == "__main__":
    unittest.main()

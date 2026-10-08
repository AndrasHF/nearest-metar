import csv
import gzip
import io
import json
import socket
import sys
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

import app as weather_app


def make_feed(rows):
    output = io.StringIO()
    fields = [
        "raw_text", "station_id", "observation_time", "latitude", "longitude",
        "temp_c", "dewpoint_c", "wind_speed_kt", "wind_gust_kt",
        "visibility_statute_mi", "altim_in_hg",
    ]
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

    def test_csv_visibility_is_preserved_by_endpoint(self):
        for visibility, expected in (
            ("10+", "10+"), ("6+", "6+"), ("2.5", 2.5), ("0", 0.0),
            ("", None), ("M", None), (None, None), ("unknown", None),
            ("unknown+", None), ("-10+", None), ("inf+", None),
        ):
            with self.subTest(visibility=visibility):
                observations = weather_app.parse_metar_csv(make_feed([{
                    "raw_text": "METAR TEST", "station_id": "TEST",
                    "latitude": "47.44", "longitude": "19.26",
                    "visibility_statute_mi": visibility,
                }]))
                with (
                    patch.object(weather_app, "fetch_observations", return_value=observations),
                    patch.object(weather_app, "fetch_station_metadata", return_value={}),
                    patch.object(weather_app, "reverse_geocode", return_value={}),
                ):
                    response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["stations"][0]["visibility_mi"], expected)

    def test_present_visibility_accepts_alias(self):
        for visibility, expected in (("10+", "10+"), ("3", 3.0), (3, 3.0), (None, None)):
            with self.subTest(visibility=visibility):
                row = {
                    "station_id": "TEST", "_lat": 47.44, "_lon": 19.26,
                    "visibility_statute_mi": "M", "visib": visibility,
                }
                self.assertEqual(weather_app.present(row, 1)["visibility_mi"], expected)

    def test_rejects_invalid_coordinates(self):
        response = weather_app.app.test_client().get("/api/metar?lat=999&lon=nope")
        self.assertEqual(response.status_code, 400)

    def test_invalid_station_coordinates_do_not_hide_valid_weather(self):
        rows = [
            {"raw_text": "METAR GOOD", "station_id": "GOOD", "latitude": "47.5", "longitude": "19.1"},
            {"raw_text": "METAR ZERO", "station_id": "ZERO", "latitude": "0", "longitude": "0"},
        ]
        for field in ("latitude", "longitude"):
            for invalid in ("NaN", "inf", "-inf", "1e309", "-1e309", "", "M", "invalid"):
                rows.append({
                    "station_id": f"BAD{len(rows)}", "latitude": "47.5", "longitude": "19.1",
                    field: invalid,
                })
        for latitude, longitude in (("90.1", "19.1"), ("-90.1", "19.1"), ("47.5", "180.1"), ("47.5", "-180.1")):
            rows.append({"station_id": f"BAD{len(rows)}", "latitude": latitude, "longitude": longitude})
        observations = weather_app.parse_metar_csv(make_feed(rows))
        with (
            patch.object(weather_app, "fetch_observations", return_value=observations),
            patch.object(weather_app, "fetch_station_metadata", return_value={}),
            patch.object(weather_app, "reverse_geocode", return_value={}),
            patch.object(weather_app, "NEAREST_STATION_COUNT", 2),
            patch.object(weather_app.app.logger, "exception"),
        ):
            response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.get_data(as_text=True), parse_constant=self.fail)
        self.assertEqual([station["station"] for station in data["stations"]], ["GOOD", "ZERO"])
        self.assertEqual([row["station_id"] for row in observations], ["GOOD", "ZERO"])

    def test_parser_preserves_coordinate_boundaries_and_zero(self):
        coordinates = ((-90, -180), (-90, 180), (90, -180), (90, 180), (0, 0))
        rows = [
            {"station_id": f"VALID{index}", "latitude": str(lat), "longitude": str(lon)}
            for index, (lat, lon) in enumerate(coordinates)
        ]
        observations = weather_app.parse_metar_csv(make_feed(rows))
        self.assertEqual([(row["_lat"], row["_lon"]) for row in observations], list(coordinates))

    def test_non_finite_measurements_are_missing_in_valid_json(self):
        fields = {
            "temp_c": "temperature_c", "dewpoint_c": "dewpoint_c",
            "wind_speed_kt": "wind_speed_kt", "wind_gust_kt": "wind_gust_kt",
            "visibility_statute_mi": "visibility_mi", "altim_in_hg": "altimeter_in_hg",
        }
        for invalid in ("NaN", "inf", "-inf", "1e309", "-1e309"):
            with self.subTest(invalid=invalid):
                rows = [
                    {
                        "raw_text": "METAR BAD", "station_id": "BAD", "latitude": "47.5", "longitude": "19.1",
                        **dict.fromkeys(fields, invalid),
                    },
                    {
                        "raw_text": "METAR GOOD", "station_id": "GOOD", "latitude": "47.6", "longitude": "19.1",
                        **dict.fromkeys(fields, "0"),
                    },
                ]
                observations = weather_app.parse_metar_csv(make_feed(rows))
                metadata = {"BAD": {"elevation_m": float(invalid)}, "GOOD": {"elevation_m": 0}}
                with (
                    patch.object(weather_app, "fetch_observations", return_value=observations),
                    patch.object(weather_app, "fetch_station_metadata", return_value=metadata),
                    patch.object(weather_app, "reverse_geocode", return_value={}),
                    patch.object(weather_app, "NEAREST_STATION_COUNT", 2),
                ):
                    response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
                self.assertEqual(response.status_code, 200)
                # Python's default decoder accepts NaN/Infinity; reject them
                # explicitly to enforce the JSON contract used by browsers.
                data = json.loads(response.get_data(as_text=True), parse_constant=self.fail)
                stations = {station["station"]: station for station in data["stations"]}
                self.assertEqual(set(stations), {"BAD", "GOOD"})
                self.assertEqual(stations["BAD"]["raw"], "METAR BAD")
                for field in (*fields.values(), "station_elevation_m"):
                    self.assertIsNone(stations["BAD"][field], field)
                    self.assertEqual(stations["GOOD"][field], 0, field)

    def test_metadata_failure_does_not_hide_valid_weather(self):
        observation = {
            "station_id": "LHBP", "_lat": 47.44, "_lon": 19.26,
            "raw_text": "LHBP TEST", "temp_c": "12",
        }
        for error in (urllib.error.URLError("catalog unavailable"), ValueError("bad cache")):
            with (
                self.subTest(error=error),
                patch.object(weather_app, "fetch_observations", return_value=[observation]),
                patch.object(weather_app, "fetch_station_metadata", side_effect=error),
                patch.object(weather_app, "reverse_geocode", return_value={"name": "Budapest"}),
                patch.object(weather_app.app.logger, "warning"),
            ):
                response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
            self.assertEqual(response.status_code, 200)
            station = response.get_json()["stations"][0]
            self.assertEqual(station["raw"], "LHBP TEST")
            self.assertEqual(station["temperature_c"], 12)
            self.assertIsNone(station["station_name"])

    def test_geocode_failure_returns_weather_with_coordinates(self):
        observation = {"station_id": "LHBP", "_lat": 47.44, "_lon": 19.26}
        with (
            patch.object(weather_app, "fetch_observations", return_value=[observation]),
            patch.object(weather_app, "fetch_station_metadata", return_value={}),
            patch.object(weather_app, "reverse_geocode", side_effect=OSError("cache unwritable")),
            patch.object(weather_app.app.logger, "warning"),
        ):
            response = weather_app.app.test_client().get("/api/metar?lat=47.5&lon=19.1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stations"][0]["station"], "LHBP")
        self.assertEqual(response.get_json()["browser_location"], {
            "name": "47.5000, 19.1000", "latitude": 47.5, "longitude": 19.1,
        })

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

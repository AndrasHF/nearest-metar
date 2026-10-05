import gzip
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import app as weather_app


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.now = 1000.0
        self.wall_start = time.time()
        self.stack.enter_context(patch.object(weather_app, "CACHE_DIRECTORY", self.directory))
        self.stack.enter_context(patch.object(weather_app.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(weather_app.time, "time", side_effect=self.wall_time))
        self.stack.enter_context(patch.object(weather_app.app.logger, "warning"))

    def wall_time(self):
        return self.wall_start + self.now - 1000.0


class StationMetadataTests(ServiceTests):
    def setUp(self):
        super().setUp()
        self.cache_file = self.directory / "stations.json"
        self.stack.enter_context(patch.object(weather_app, "STATION_CACHE_FILE", self.cache_file))
        self.stack.enter_context(patch.object(weather_app, "_stations", None))
        self.stack.enter_context(patch.object(weather_app, "_stations_expires_at", 0.0))
        self.records = [{"icaoId": "LHBP", "site": "Budapest", "country": "HU", "iataId": "BUD"}]
        self.download = self.stack.enter_context(patch.object(
            weather_app.urllib.request, "urlopen", side_effect=self.response,
        ))

    def response(self, request, timeout):
        return io.BytesIO(gzip.compress(json.dumps(self.records).encode()))

    def write_cache(self, stations, age=0):
        self.cache_file.write_text(json.dumps(stations), encoding="utf-8")
        modified = self.wall_time() - age
        os.utime(self.cache_file, (modified, modified))

    def test_download_persists_catalog_and_reuses_memory(self):
        first = weather_app.fetch_station_metadata()
        self.now += 100
        self.assertEqual(weather_app.fetch_station_metadata(), first)
        self.assertEqual(first["LHBP"]["name"], "Budapest")
        self.assertEqual(json.loads(self.cache_file.read_text()), first)
        self.assertEqual(self.download.call_count, 1)

    def test_fresh_disk_catalog_is_reused(self):
        stations = {"LHBP": {"name": "Cached Budapest"}}
        self.write_cache(stations, age=100)
        self.assertEqual(weather_app.fetch_station_metadata(), stations)
        self.download.assert_not_called()

    def test_memory_catalog_refreshes_after_one_day(self):
        weather_app.fetch_station_metadata()
        self.now += weather_app.STATION_CACHE_SECONDS + 1
        self.records[0]["site"] = "Updated Budapest"
        result = weather_app.fetch_station_metadata()
        self.assertEqual(result["LHBP"]["name"], "Updated Budapest")
        self.assertEqual(json.loads(self.cache_file.read_text()), result)
        self.assertEqual(self.download.call_count, 2)

    def test_loading_disk_cache_preserves_its_remaining_lifetime(self):
        self.write_cache({"LHBP": {"name": "Old name"}}, age=weather_app.STATION_CACHE_SECONDS - 10)
        weather_app.fetch_station_metadata()
        self.now += 11
        self.assertEqual(weather_app.fetch_station_metadata()["LHBP"]["name"], "Budapest")
        self.download.assert_called_once()

    def test_corrupt_or_invalid_disk_cache_is_replaced(self):
        for payload in ("{broken", "[]", '{"LHBP": "invalid"}', "{}"):
            with self.subTest(payload=payload):
                weather_app._stations = None
                weather_app._stations_expires_at = 0
                self.cache_file.write_text(payload)
                self.download.reset_mock()
                result = weather_app.fetch_station_metadata()
                self.assertEqual(result["LHBP"]["name"], "Budapest")
                self.assertEqual(json.loads(self.cache_file.read_text()), result)
                self.download.assert_called_once()

    def test_unreadable_disk_cache_is_ignored(self):
        with patch.object(weather_app, "_read_station_cache", side_effect=PermissionError("denied")):
            result = weather_app.fetch_station_metadata()
        self.assertEqual(result["LHBP"]["name"], "Budapest")
        self.download.assert_called_once()

    def test_stale_catalog_is_used_on_failure_then_retried(self):
        stations = {"LHBP": {"name": "Old name"}}
        self.write_cache(stations, age=weather_app.STATION_CACHE_SECONDS + 1)
        self.download.side_effect = urllib.error.URLError("offline")
        self.assertEqual(weather_app.fetch_station_metadata(), stations)
        self.now += weather_app.STATION_RETRY_SECONDS - 1
        self.assertEqual(weather_app.fetch_station_metadata(), stations)
        self.assertEqual(self.download.call_count, 1)
        self.now += 1
        self.download.side_effect = self.response
        self.assertEqual(weather_app.fetch_station_metadata()["LHBP"]["name"], "Budapest")
        self.assertEqual(self.download.call_count, 2)

    def test_missing_catalog_does_not_retry_every_request(self):
        self.download.side_effect = urllib.error.URLError("offline")
        self.assertEqual(weather_app.fetch_station_metadata(), {})
        self.assertEqual(weather_app.fetch_station_metadata(), {})
        self.assertEqual(self.download.call_count, 1)
        self.now += weather_app.STATION_RETRY_SECONDS
        self.download.side_effect = self.response
        self.assertEqual(weather_app.fetch_station_metadata()["LHBP"]["name"], "Budapest")
        self.assertEqual(self.download.call_count, 2)

    def test_corrupt_cache_and_failed_download_return_empty_metadata(self):
        self.cache_file.write_text("{broken")
        self.download.side_effect = urllib.error.URLError("offline")
        self.assertEqual(weather_app.fetch_station_metadata(), {})

    def test_invalid_download_does_not_replace_usable_stale_cache(self):
        stations = {"LHBP": {"name": "Old name"}}
        self.write_cache(stations, age=weather_app.STATION_CACHE_SECONDS + 1)
        self.records = []
        self.assertEqual(weather_app.fetch_station_metadata(), stations)
        self.assertEqual(json.loads(self.cache_file.read_text()), stations)

    def test_cache_write_failure_keeps_downloaded_details(self):
        with patch.object(weather_app, "_write_station_cache", side_effect=PermissionError("denied")):
            result = weather_app.fetch_station_metadata()
        self.assertEqual(result["LHBP"]["name"], "Budapest")
        self.assertEqual(weather_app.fetch_station_metadata(), result)
        self.download.assert_called_once()


class GeocodingTests(ServiceTests):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(weather_app, "_geocode_cache", OrderedDict()))
        self.stack.enter_context(patch.object(weather_app, "_geocode_lock", threading.Lock()))
        self.stack.enter_context(patch.object(weather_app, "_geocode_download_lock", threading.Lock()))
        self.sleep = self.stack.enter_context(patch.object(weather_app.time, "sleep", side_effect=self.advance))
        self.starts = []
        self.download = self.stack.enter_context(patch.object(
            weather_app.urllib.request, "urlopen", side_effect=self.response,
        ))

    def advance(self, seconds):
        self.now += seconds

    def response(self, request, timeout):
        self.starts.append(self.wall_time())
        return io.BytesIO(json.dumps({"address": {"city": "Budapest", "country": "Hungary"}}).encode())

    def test_repeated_coordinates_reuse_result_without_waiting(self):
        first = weather_app.reverse_geocode(47.5, 19.1)
        second = weather_app.reverse_geocode(47.5, 19.1)
        self.assertEqual(second, {"name": "Budapest, Hungary", "latitude": 47.5, "longitude": 19.1})
        first["name"] = "Changed by caller"
        self.assertEqual(weather_app.reverse_geocode(47.5, 19.1), second)
        self.download.assert_called_once()
        self.sleep.assert_not_called()

    def test_cached_result_returns_during_unrelated_lookup(self):
        for phase in ("throttle", "download"):
            with self.subTest(phase=phase):
                weather_app._geocode_cache.clear()
                expected = weather_app.reverse_geocode(47.5, 19.1)
                self.download.reset_mock()
                entered = threading.Event()
                release = threading.Event()

                def block():
                    entered.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("Test did not release the unrelated lookup")

                def delayed_response(request, timeout):
                    block()
                    return self.response(request, timeout)

                def delayed_sleep(seconds):
                    block()
                    self.advance(seconds)

                target = self.sleep if phase == "throttle" else self.download
                side_effect = delayed_sleep if phase == "throttle" else delayed_response
                with patch.object(target, "side_effect", side_effect):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        unrelated = pool.submit(weather_app.reverse_geocode, 48, 16)
                        try:
                            self.assertTrue(entered.wait(timeout=2))
                            cached = pool.submit(weather_app.reverse_geocode, 47.5, 19.1)
                            self.assertEqual(cached.result(timeout=1), expected)
                            self.assertFalse(unrelated.done())
                        finally:
                            release.set()
                        unrelated.result(timeout=2)
                self.download.assert_called_once()

    def test_language_is_part_of_cache_key(self):
        weather_app.reverse_geocode(47.5, 19.1, "en")
        weather_app.reverse_geocode(47.5, 19.1, "hu")
        weather_app.reverse_geocode(47.5, 19.1, "en")
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(self.download.call_args.args[0].get_header("Accept-language"), "hu")

    def test_configured_endpoint_preserves_existing_query_parameters(self):
        endpoint = "https://provider.example/reverse?key=a%2Bb%26c&tag=one&tag=two&empty=#section"
        with patch.object(weather_app, "REVERSE_GEOCODE_URL", endpoint):
            weather_app.reverse_geocode(47.5, 19.1)
        url = urllib.parse.urlsplit(self.download.call_args.args[0].full_url)
        self.assertEqual((url.scheme, url.netloc, url.path, url.fragment), (
            "https", "provider.example", "/reverse", "section",
        ))
        self.assertEqual(urllib.parse.parse_qs(url.query, keep_blank_values=True), {
            "key": ["a+b&c"], "tag": ["one", "two"], "empty": [""],
            "lat": ["47.5"], "lon": ["19.1"], "format": ["jsonv2"],
            "zoom": ["10"], "addressdetails": ["1"],
        })

    def test_request_parameters_override_configured_endpoint_defaults(self):
        endpoint = "https://provider.example/reverse?lat=0&lon=0&format=xml&zoom=1&addressdetails=0"
        with patch.object(weather_app, "REVERSE_GEOCODE_URL", endpoint):
            weather_app.reverse_geocode(47.5, 19.1)
        url = urllib.parse.urlsplit(self.download.call_args.args[0].full_url)
        self.assertEqual(urllib.parse.parse_qs(url.query), {
            "lat": ["47.5"], "lon": ["19.1"], "format": ["jsonv2"],
            "zoom": ["10"], "addressdetails": ["1"],
        })

    def test_cached_labels_expire(self):
        weather_app.reverse_geocode(47.5, 19.1)
        self.now += weather_app.GEOCODE_CACHE_SECONDS
        weather_app.reverse_geocode(47.5, 19.1)
        self.assertEqual(self.download.call_count, 2)

    def test_result_cache_is_bounded_and_evicts_least_recently_used(self):
        with patch.object(weather_app, "GEOCODE_CACHE_SIZE", 2):
            weather_app.reverse_geocode(47, 19)
            weather_app.reverse_geocode(48, 19)
            weather_app.reverse_geocode(47, 19)
            weather_app.reverse_geocode(49, 19)
            weather_app.reverse_geocode(48, 19)
        self.assertEqual(self.download.call_count, 4)
        self.assertEqual(len(weather_app._geocode_cache), 2)

    def test_uncached_requests_are_at_least_one_second_apart(self):
        weather_app.reverse_geocode(47, 19)
        self.now += 0.25
        weather_app.reverse_geocode(48, 19)
        self.assertAlmostEqual(self.starts[1] - self.starts[0], 1.0)
        self.sleep.assert_called_once_with(0.75)

    def test_failed_requests_also_consume_rate_limit(self):
        def fail(request, timeout):
            self.starts.append(self.wall_time())
            raise urllib.error.URLError("offline")

        self.download.side_effect = fail
        with self.assertRaises(urllib.error.URLError):
            weather_app.reverse_geocode(47, 19)
        self.download.side_effect = self.response
        weather_app.reverse_geocode(47, 19)
        self.assertEqual(self.download.call_count, 2)
        self.assertAlmostEqual(self.starts[1] - self.starts[0], 1.0)

    def test_concurrent_identical_requests_download_once(self):
        barrier = threading.Barrier(4)

        def locate(_):
            barrier.wait(timeout=5)
            return weather_app.reverse_geocode(47.5, 19.1)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(locate, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.download.assert_called_once()

    def test_concurrent_different_locations_are_throttled(self):
        barrier = threading.Barrier(4)

        def locate(latitude):
            barrier.wait(timeout=5)
            return weather_app.reverse_geocode(latitude, 19)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(locate, range(47, 51)))
        self.assertEqual(self.download.call_count, 4)
        for previous, current in zip(self.starts, self.starts[1:]):
            self.assertGreaterEqual(current - previous, 1.0)

    def test_concurrent_processes_honor_the_shared_rate_limit(self):
        weather_app.reverse_geocode(47, 19)
        # A fresh interpreter has no in-memory cache or lock in common with us.
        # Both workers use a fake clock so this test never sleeps or uses NOAA.
        script = '''
import io, json, sys
from pathlib import Path
from unittest.mock import patch
import app
app.CACHE_DIRECTORY = Path(sys.argv[1])
clock = [float(sys.argv[2])]
def sleep(seconds):
    clock[0] += seconds
def response(request, timeout):
    print(clock[0])
    return io.BytesIO(b'{"address":{"city":"Vienna"}}')
with patch.object(app.time, "time", side_effect=lambda: clock[0]), \\
     patch.object(app.time, "sleep", side_effect=sleep), \\
     patch.object(app.urllib.request, "urlopen", side_effect=response):
    app.reverse_geocode(48, 16)
'''
        def locate(_):
            completed = subprocess.run(
                [sys.executable, "-c", script, str(self.directory), str(self.wall_time())],
                cwd=Path(weather_app.__file__).parent, capture_output=True, text=True, timeout=10, check=True,
            )
            return float(completed.stdout.strip())

        with ThreadPoolExecutor(max_workers=3) as pool:
            starts = sorted(pool.map(locate, range(3)))
        self.assertEqual([start - self.starts[0] for start in starts], [1.0, 2.0, 3.0])

    def test_persisted_limiter_contains_no_coordinates_or_labels(self):
        weather_app.reverse_geocode(47.5, 19.1)
        with sqlite3.connect(self.directory / "geocoding.sqlite3") as connection:
            columns = connection.execute("PRAGMA table_info(rate_limit)").fetchall()
            rows = connection.execute("SELECT * FROM rate_limit").fetchall()
        self.assertEqual([column[1] for column in columns], ["id", "next_allowed"])
        self.assertEqual(rows, [(1, self.wall_time() + 1)])

import json
from pathlib import Path
import unittest

import quickjs


class WindRenderingTests(unittest.TestCase):
    def setUp(self):
        self.javascript = quickjs.Context()
        # Execute the actual app script with a minimal DOM, then assert what
        # render() writes to the wind element rather than duplicating its logic.
        self.javascript.eval('''
            const elements = {};
            const document = {
              getElementById(id) {
                return elements[id] ||= { textContent: "", addEventListener() {} };
              }
            };
        ''')
        script = Path(__file__).resolve().parents[1] / "static" / "app.js"
        self.javascript.eval(script.read_text(encoding="utf-8"))

    def wind_text(self, direction=None, speed=None, gust=None):
        data = {"wind_direction": direction, "wind_speed_kt": speed, "wind_gust_kt": gust}
        self.javascript.eval(f"render({json.dumps(data)});")
        return self.javascript.eval('document.getElementById("wind").textContent')

    def test_zero_speed_is_calm(self):
        for direction in ("0", 0, None, "360"):
            with self.subTest(direction=direction):
                self.assertEqual(self.wind_text(direction, 0), "CALM")

    def test_variable_direction_has_no_degree_symbol(self):
        self.assertEqual(self.wind_text("VRB", 3), "VRB / 3 KT")
        self.assertEqual(self.wind_text("VRB", 3, 10), "VRB / 3 G10 KT")

    def test_numeric_directions_preserve_degrees_and_gusts(self):
        for direction, expected in (("270", "270°"), (360, "360°"), (0, "0°"), ("0", "0°")):
            with self.subTest(direction=direction):
                self.assertEqual(self.wind_text(direction, 12, 20), f"{expected} / 12 G20 KT")

    def test_missing_or_invalid_direction_is_not_variable(self):
        for direction in (None, "", "M", "unknown"):
            with self.subTest(direction=direction):
                self.assertEqual(self.wind_text(direction, 4), "— / 4 KT")

    def test_missing_speed_is_unreported(self):
        self.assertEqual(self.wind_text("VRB"), "—")

    def test_gusting_wind_is_not_calm(self):
        self.assertEqual(self.wind_text(0, 0, 8), "0° / 0 G8 KT")

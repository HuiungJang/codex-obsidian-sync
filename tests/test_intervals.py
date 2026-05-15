from __future__ import annotations

import unittest

from codex_obsidian_sync.intervals import format_interval, parse_interval


class IntervalTests(unittest.TestCase):
    def test_parse_interval_supports_human_friendly_units(self) -> None:
        self.assertEqual(parse_interval("10s"), 10)
        self.assertEqual(parse_interval("1m"), 60)
        self.assertEqual(parse_interval("2h"), 7200)
        self.assertEqual(parse_interval("15"), 15)

    def test_format_interval_prefers_larger_units_when_exact(self) -> None:
        self.assertEqual(format_interval(60), "1m (60s)")
        self.assertEqual(format_interval(7200), "2h (7200s)")
        self.assertEqual(format_interval(15), "15s")


if __name__ == "__main__":
    unittest.main()

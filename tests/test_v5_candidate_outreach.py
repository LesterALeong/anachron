"""Negative capability checks for the local v5 UNSENT renderer."""

from __future__ import annotations

import unittest
from pathlib import Path


class V5CandidateOutreachTests(unittest.TestCase):
    def test_renderer_has_no_recipient_or_dispatch_capability(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools/render_v5_measurement_unsent_outreach.py").read_text(encoding="utf-8").lower()
        for forbidden in ("import requests", "import socket", "import smtplib", "import urllib", "import webbrowser", "--send", "--upload"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"unsent"', source)


if __name__ == "__main__":
    unittest.main()

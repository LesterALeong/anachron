"""Negative capability checks for the v4 UNSENT outreach renderer."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V4CandidateOutreachTests(unittest.TestCase):
    def test_renderer_has_no_external_transport_or_recipient_schema(self) -> None:
        source = (ROOT / "tools/render_v4_measurement_unsent_outreach.py").read_text(
            encoding="utf-8"
        ).lower()
        for forbidden in (
            "import requests",
            "import socket",
            "import smtplib",
            "import urllib",
            "import webbrowser",
            "import subprocess",
            '"recipient":',
            "--send",
            "--upload",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"status": "unsent"', source)


if __name__ == "__main__":
    unittest.main()

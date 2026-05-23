"""Tests for CLI helper utilities."""

from datetime import datetime

from investdaytip.main import _default_export_html_filename


def test_default_export_html_filename_format():
    now = datetime(2026, 5, 23, 9, 7)
    assert _default_export_html_filename(now) == "investDayTip-20260523-0907.html"

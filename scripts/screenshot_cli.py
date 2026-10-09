"""Regenerate docs/screenshot-CLI.png from a real CLI run.

Runs the actual CLI (live market data — never mock) with a recording Rich
console, exports the terminal as SVG, then renders that SVG to PNG with headless
Chrome. Run from the repo root:

    PYTHONPATH=src .venv/bin/python scripts/screenshot_cli.py
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text

REPO = Path(__file__).resolve().parents[1]
SVG = REPO / "docs" / "screenshot-cli.svg"
PNG = REPO / "docs" / "screenshot-CLI.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Matches the committed capture: wide enough for the full ratings table.
WIDTH = 176


def strip_window_chrome(svg: str) -> str:
    """Drop Rich's fake macOS window bar (title + traffic lights).

    The committed capture starts at the ASCII logo, not at a title bar, and
    the bar costs 41px of top padding on top of an off-centre origin.
    """
    uid = re.search(r'class="rich-terminal"', svg)
    if not uid:
        return svg
    # The chrome is one <rect ...rx="8"/> + <text ...title...> line, then the
    # traffic-lights <g transform="translate(26,22)">…</g>.
    svg = re.sub(r'<rect fill="#292929"[^>]*/><text class="[^"]*-title".*?</text>\n', "", svg, count=1)
    svg = re.sub(r'<g transform="translate\(26,22\)">\s*<circle[^>]*/>\s*<circle[^>]*/>\s*<circle[^>]*/>\s*</g>\s*', "", svg, count=1)
    # Content group now starts at the origin, and the viewBox is the clipped
    # terminal size rather than the padded window.
    clip = re.search(
        r'<clipPath id="[^"]*-clip-terminal">\s*<rect x="0" y="0" width="([\d.]+)" height="([\d.]+)"',
        svg,
    )
    if clip:
        w, h = clip.group(1), clip.group(2)
        svg = re.sub(
            r'(<svg class="rich-terminal" viewBox=")[^"]*(")',
            rf"\g<1>0 0 {w} {h}\g<2>",
            svg,
            count=1,
        )
        svg = svg.replace('<g transform="translate(9, 41)"', '<g transform="translate(0, 0)"', 1)
    return svg


def run_cli(argv: list[str]) -> str:
    """Run the real CLI against live data and return its rendered SVG.

    The CLI runs as a **subprocess with piped stdout**: Rich's `Live` detects
    that it has no terminal and prints each bar's final state instead of
    redrawing in place, so nothing is captured mid-refresh. The ANSI that
    comes back is then replayed into a recording console for the SVG export.
    """
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"), COLUMNS=str(WIDTH))
    proc = subprocess.run(
        [sys.executable, "-m", "investdaytip.main", *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )
    if proc.returncode != 0:
        sys.exit(f"CLI exited with {proc.returncode}: {proc.stderr[-2000:]}")

    recorded = Console(record=True, width=WIDTH, file=io.StringIO())
    recorded.print(Text.from_ansi(proc.stdout))
    return strip_window_chrome(recorded.export_svg(title="InvestDayTip", clear=False))


def svg_to_png(svg: Path, png: Path) -> None:
    if not Path(CHROME).exists():
        sys.exit(f"Chrome not found at {CHROME}")
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text(encoding="utf-8"))
    if not box:
        sys.exit("no viewBox in the exported SVG")
    w, h = (int(float(box.group(1))), int(float(box.group(2))))
    subprocess.run(
        [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            f"--window-size={w},{h}",
            f"--screenshot={png}",
            f"file://{svg}",
        ],
        check=True,
        capture_output=True,
    )


def main_entry() -> None:
    argv = sys.argv[1:] or ["-n", "5"]
    svg = run_cli(argv)
    SVG.write_text(svg, encoding="utf-8")
    svg_to_png(SVG, PNG)
    size = PNG.stat().st_size / 1024
    print(f"wrote {SVG.relative_to(REPO)} and {PNG.relative_to(REPO)} ({size:.0f} KB)")


if __name__ == "__main__":
    main_entry()

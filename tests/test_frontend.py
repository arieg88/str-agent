"""Map page checks: the JavaScript unit tests (run with Node, no npm packages) and the CDN tags."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stragent.api.app import STATIC_DIR

JS_TESTS = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_javascript_unit_tests():
    """tests/js/*.test.mjs, including the https-only rule for links built from data."""
    assert JS_TESTS
    run = subprocess.run(
        ["node", "--test", *map(str, JS_TESTS)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_cdn_tags_are_pinned_with_integrity_hashes():
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tags = re.findall(r"<(?:script|link)\b[^>]*https?://[^>]*>", page)
    assert len(tags) == 2  # Leaflet's stylesheet and script
    for tag in tags:
        assert "leaflet@1.9.4" in tag
        assert re.search(r'integrity="sha(256|384|512)-[A-Za-z0-9+/=]+"', tag), tag
        assert 'crossorigin=""' in tag, tag

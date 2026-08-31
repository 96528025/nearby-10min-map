"""Regression checks for required Overture/Foursquare licensing files."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_apache_2_license_matches_canonical_text():
    license_bytes = (ROOT / "LICENSES" / "Apache-2.0.txt").read_bytes()

    # Canonical https://www.apache.org/licenses/LICENSE-2.0.txt, retrieved
    # 2026-08-31. The leading newline is part of the official text.
    assert hashlib.sha256(license_bytes).hexdigest() == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )


def test_notice_retains_attribution_and_describes_project_changes():
    notice = (ROOT / "NOTICE").read_text()

    required = (
        "Overture Maps Foundation",
        "2026-08-19.0",
        "release remains unknown",
        "Copyright 2024 Foursquare Labs, Inc.",
        "© 2026 Foursquare Labs, Inc.",
        "LICENSES/Apache-2.0.txt",
        "filters records by confidence",
        "deduplicates Overture records against OpenStreetMap",
        "clips the result to the selected displayed boundary",
        "rounds retained coordinates to six decimal places",
        "Project modification notice — 2026-08-31",
    )
    for text in required:
        assert text in notice


def test_audit_distinguishes_production_release_from_historical_sample():
    audit = (ROOT / "docs" / "ATTRIBUTION_AUDIT.md").read_text()

    assert "Production release selected" in audit
    assert "2026-08-19.0" in audit
    assert "original audit sample" in audit
    assert "2026-07-22.0" in audit
    assert "release is unknown" in audit

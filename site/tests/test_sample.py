import subprocess
import sys

from wbot_site.build import SAMPLE_DIR, SITE_DIR

GENERATOR = SITE_DIR / "tools" / "make_sample.py"


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_committed_sample_matches_the_generator(tmp_path):
    """Fails if sample-stats/ was edited by hand or the generator changed without a rerun."""
    out = tmp_path / "sample"
    subprocess.run([sys.executable, str(GENERATOR), "--out", str(out)], check=True, capture_output=True)
    assert files(out) == files(SAMPLE_DIR)


def test_sample_is_marked_synthetic():
    assert b'"synthetic": true' in (SAMPLE_DIR / "site" / "meta.json").read_bytes()

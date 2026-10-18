"""The committed sample stats (site/sample-stats/) are what make_sample.py writes."""

import make_sample


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_committed_sample_matches_the_generator(tmp_path):
    """Fails if sample-stats/ was edited by hand, or the generator or the pipeline's output changed
    without a rerun (cd pipeline && uv run python tests/make_sample.py)."""
    out = tmp_path / "sample"
    make_sample.build(out)
    assert files(out) == files(make_sample.SAMPLE_DIR)


def test_sample_is_marked_synthetic():
    assert b'"synthetic": true' in (make_sample.SAMPLE_DIR / "site" / "meta.json").read_bytes()

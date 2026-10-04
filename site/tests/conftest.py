import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wbot_site.build import SAMPLE_DIR, build

BUILD_TIME = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def make_build(stats_dir, out, env="dev", **kwargs):
    return build(stats_dir, out, env, code_commit="abc1234", stats_commit="def5678", build_time=BUILD_TIME, **kwargs)


def html_pages(out: Path) -> list[Path]:
    return sorted(out.rglob("*.html"))


@pytest.fixture(scope="session")
def dev_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("dev") / "dist"
    make_build(SAMPLE_DIR, out, "dev")
    return out


@pytest.fixture
def real_looking_stats(tmp_path):
    """A copy of the sample marked as real, to exercise a prod build."""
    stats = tmp_path / "stats"
    shutil.copytree(SAMPLE_DIR, stats)
    meta_path = stats / "site" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["synthetic"] = False
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return stats

import json
import subprocess

import pytest

from wbot_pipeline import publish
from wbot_pipeline.config import COMMIT_EMAIL, load_settings


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def remote(tmp_path):
    """A bare 'stats repo' with a README on main, and the pipeline's clone of it."""
    bare = tmp_path / "stats.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(seed)], check=True)
    (seed / "README.md").write_text("stats\n")
    git(seed, "add", "README.md")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "--quiet", "-m", "init")
    git(seed, "push", "--quiet", "origin", "HEAD:main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(clone)], check=True)
    return bare, clone


def output(root, value, synthetic=False):
    (root / "site" / "routes").mkdir(parents=True, exist_ok=True)
    (root / "csv").mkdir(parents=True, exist_ok=True)
    (root / "site" / "meta.json").write_text(json.dumps({"schema": 1, "synthetic": synthetic, "v": value}))
    (root / "csv" / "system_daily.csv").write_text(f"date,n\n2026-10-06,{value}\n")
    return root


def test_off_by_default(tmp_path):
    settings = load_settings({}, data_dir=tmp_path, derived_dir=tmp_path)
    assert publish.publish(settings, output(tmp_path / "out", 1), message="m") == "disabled"


def test_publish_commits_pushes_and_replaces(tmp_path, remote):
    bare, clone = remote
    settings = load_settings({}, data_dir=tmp_path, derived_dir=tmp_path, publish=True, stats_repo=clone)
    out = output(tmp_path / "out", 1)
    (out / "site" / "routes" / "old.json").write_text("{}")
    assert publish.publish(settings, out, message="Stats through 2026-10-06") == "pushed"
    assert publish.publish(settings, out, message="again") == "unchanged"
    (out / "site" / "routes" / "old.json").unlink()
    output(out, 2)
    assert publish.publish(settings, out, message="Stats through 2026-10-07") == "pushed"
    log = git(bare, "log", "--format=%s|%ae", "main")
    assert log.splitlines() == [f"Stats through 2026-10-07|{COMMIT_EMAIL}", f"Stats through 2026-10-06|{COMMIT_EMAIL}",
                                "init|t@example.invalid"]
    files = git(bare, "ls-tree", "-r", "--name-only", "main").split()
    assert files == ["README.md", "csv/system_daily.csv", "site/meta.json"]     # stale file removed, README kept


def test_refuses_synthetic(tmp_path, remote):
    _, clone = remote
    settings = load_settings({}, data_dir=tmp_path, derived_dir=tmp_path, publish=True, stats_repo=clone)
    with pytest.raises(publish.PublishError):
        publish.publish(settings, output(tmp_path / "out", 1, synthetic=True), message="m")

"""Publish the site files to the public stats repo: replace site/ and csv/ in a local
clone, commit if anything changed, push.

Off unless WBOT_PIPELINE_PUBLISH=1. The clone belongs to the pipeline: it is reset to
the remote before every publish. Pushing uses a deploy key scoped to that repo only
(WBOT_STATS_SSH_KEY); the key's contents are never logged.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .config import COMMIT_EMAIL, COMMIT_NAME, Settings

log = logging.getLogger(__name__)

PUBLISHED = ("site", "csv")
BRANCH = "main"


class PublishError(Exception):
    pass


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {result.stderr.strip()[-2000:]}")
    return result.stdout


def git_env(settings: Settings) -> dict:
    env = dict(os.environ)
    if settings.ssh_key:
        env["GIT_SSH_COMMAND"] = f'ssh -i "{settings.ssh_key}" -o IdentitiesOnly=yes -o BatchMode=yes'
    return env


def publish(settings: Settings, out: Path, *, message: str) -> str:
    """Returns 'disabled', 'unchanged' or 'pushed'."""
    if not settings.publish:
        log.info("publishing is off (WBOT_PIPELINE_PUBLISH); output left in %s", out)
        return "disabled"
    repo = settings.stats_repo
    if repo is None or not (repo / ".git").exists():
        raise PublishError(f"no stats repo clone at {repo}")
    meta = json.loads((out / "site" / "meta.json").read_text(encoding="utf-8"))
    if meta.get("synthetic") is not False:
        raise PublishError("refusing to publish data that isn't marked synthetic: false")

    env = git_env(settings)
    _git(repo, "fetch", "--quiet", "origin", BRANCH, env=env)
    _git(repo, "checkout", "--quiet", "-B", BRANCH, f"origin/{BRANCH}")
    _git(repo, "reset", "--quiet", "--hard", f"origin/{BRANCH}")
    for name in PUBLISHED:
        target = repo / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(out / name, target)
    _git(repo, "add", "--all", "--", *PUBLISHED)
    if not _git(repo, "status", "--porcelain", "--", *PUBLISHED).strip():
        log.info("stats unchanged; nothing to publish")
        return "unchanged"
    _git(repo, "-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
         "commit", "--quiet", "-m", message)
    _git(repo, "push", "--quiet", "origin", f"HEAD:{BRANCH}", env=env)
    log.info("published: %s", message)
    return "pushed"

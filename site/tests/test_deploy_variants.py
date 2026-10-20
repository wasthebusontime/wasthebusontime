"""The patterns preview on :8080 (deploy/patterns/) is a variant of the dev site on :80: its
units and Caddy site may differ only where they must, so a change to the main ones can't
silently miss them."""

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def unit(path: Path) -> dict[tuple[str, str], list[str]]:
    """{(section, key): [values]} of a systemd unit file, comments left out."""
    out, section = {}, ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line
            continue
        key, _, value = line.partition("=")
        out.setdefault((section, key), []).append(value)
    return out


@pytest.mark.parametrize(("base", "variant", "differ"), [
    ("systemd/wbot-dev-poll.service", "patterns/wbot-patterns-poll.service",
     {"Description", "ExecStart", "Environment", "ReadWritePaths"}),
    ("systemd/wbot-dev-rebuild.service", "patterns/wbot-patterns-rebuild.service",
     {"Description", "ExecStart", "Environment", "ReadWritePaths"}),
    ("systemd/wbot-dev-poll.timer", "patterns/wbot-patterns-poll.timer", {"Description"}),
])
def test_variant_differs_only_where_it_must(base, variant, differ):
    a, b = unit(DEPLOY / base), unit(DEPLOY / variant)
    changed = {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}
    assert {key for _, key in changed} <= differ


@pytest.mark.parametrize("name", ["wbot-patterns-poll.service", "wbot-patterns-rebuild.service"])
def test_preview_builds_the_patterns_branch_from_its_own_data(name):
    u = unit(DEPLOY / "patterns" / name)
    env = " ".join(u[("[Service]", "Environment")])
    for setting in ("WBOT_DEV_ROOT=/srv/wbot-patterns", "WBOT_CODE_BRANCH=patterns", "WBOT_STATS_URL= ",
                    '"WBOT_DEV_LABEL=PATTERNS PREVIEW"'):
        assert setting in env
    assert u[("[Service]", "ReadWritePaths")] == ["/srv/wbot-patterns"]


def site_block(text: str) -> str:
    """The first site block of a Caddyfile, comments and blank lines left out."""
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    start = next(i for i, line in enumerate(lines) if line.endswith("{") and not line.startswith("\t"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def test_caddy_site_is_the_dev_site_on_its_own_root():
    main = site_block((DEPLOY / "Caddyfile").read_text(encoding="utf-8"))
    preview = site_block((DEPLOY / "patterns" / "patterns.caddy").read_text(encoding="utf-8"))
    assert preview.startswith(":8080 {")
    # Same site but for the port and the root; the basemap tiles are shared.
    as_main = re.sub(r"/srv/wbot-patterns/", "/srv/wbot-dev/", preview.replace(":8080 {", ":80 {", 1))
    assert as_main == main
    assert "root * /srv/wbot-dev/tiles" in preview
    assert "import /etc/caddy/conf.d/*.caddy" in (DEPLOY / "Caddyfile").read_text(encoding="utf-8")

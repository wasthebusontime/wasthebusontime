"""The patterns preview's units (deploy/patterns/) are variants of the main ones: they may
differ only in the settings listed here, so a change to a main unit can't silently miss them."""

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
    ("systemd/wbot-pipeline.service", "patterns/wbot-pipeline-patterns.service",
     {"Description", "After", "OnSuccess", "ExecStart", "EnvironmentFile", "WorkingDirectory", "ReadWritePaths"}),
    ("systemd/wbot-pipeline.timer", "patterns/wbot-pipeline-patterns.timer",
     {"Description", "OnCalendar", "RandomizedDelaySec"}),
    ("devcopy/wbot-devcopy.service", "patterns/wbot-devcopy-patterns.service",
     {"Description", "ExecStart", "EnvironmentFile"}),
])
def test_variant_differs_only_where_it_must(base, variant, differ):
    a, b = unit(DEPLOY / base), unit(DEPLOY / variant)
    changed = {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}
    assert {key for _, key in changed} <= differ


def test_preview_never_touches_the_main_output():
    u = unit(DEPLOY / "patterns" / "wbot-pipeline-patterns.service")
    assert u[("[Service]", "ReadWritePaths")] == ["/srv/wbot/data/derived-patterns"]
    assert u[("[Service]", "EnvironmentFile")] == ["/etc/wasthebusontime/pipeline-patterns.env"]
    assert "wbot-pipeline.service" in u[("[Unit]", "After")][0].split()
    env = (DEPLOY / "patterns" / "pipeline-patterns.env.example").read_text(encoding="utf-8")
    settings = dict(line.split("=", 1) for line in env.splitlines() if line and not line.startswith("#"))
    assert settings["WBOT_PIPELINE_PREVIEW"] == "1"
    assert not {"WBOT_STATS_REPO", "WBOT_STATS_SSH_KEY", "WBOT_HC_PIPELINE"} & settings.keys()

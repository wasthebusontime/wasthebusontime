import duckdb
import pytest
from conftest import DAY
from synth import SimAlert

from wbot_pipeline import alerts, events, static, timeutil

O = timeutil.origin(DAY)
H = 3600


def test_detour_time_is_its_active_period_while_in_the_feed():
    # Posted for 07:00 to 10:00, but taken out of the feed after the 08:00 snapshot.
    assert alerts.windows([(O + 7 * H, O + 10 * H)], O + 6 * H, O + 8 * H) == [(O + 7 * H, O + 8 * H + 300)]
    # Open-ended periods end with the feed too.
    assert alerts.windows([(O + 7 * H, 0)], O + 6 * H, O + 8 * H) == [(O + 7 * H, O + 8 * H + 300)]
    assert alerts.windows([(0, O + 7 * H)], O + 6 * H, O + 8 * H) == [(O + 6 * H, O + 7 * H)]


def test_detour_without_active_period_is_active_while_in_the_feed():
    assert alerts.windows([], O + 6 * H, O + 8 * H) == [(O + 6 * H, O + 8 * H + 300)]


def test_period_outside_the_feed_time_counts_for_nothing():
    assert alerts.windows([(O + 2 * H, O + 3 * H)], O + 6 * H, O + 8 * H) == []


def test_extra_routes(tmp_path):
    path = tmp_path / "routes.toml"
    path.write_text('[[alert]]\nid = "719"\nroutes = ["24", "32"]\nnote = "Crosby Loop"\n')
    assert alerts.load_extra_routes(path) == {"719": ["24", "32"]}
    assert alerts.load_extra_routes(tmp_path / "missing.toml") == {}
    path.write_text('[[alert]]\nid = "719"\nroutes = "24"\n')
    with pytest.raises(ValueError):
        alerts.load_extra_routes(path)


def test_shipped_extra_routes_are_valid():
    extra = alerts.load_extra_routes()
    assert extra["719"] == ["24", "32"] and extra["720"] == ["13", "16"]


def test_alerts_are_kept_with_the_day(settings, feed):
    feed.alerts = [SimAlert("a1", ["R901"], (O + 7 * H, O + 8 * H), periods=[(O + 7 * H + 1800, O + 10 * H)],
                            header="Route 901 on detour", description="Stops E202 and E203 are closed."),
                   SimAlert("a2", ["R902"], (O + 7 * H, O + 9 * H), effect=alerts.R.Alert.SIGNIFICANT_DELAYS)]
    feed.write()
    events.process_date(settings, static.list_versions(settings.static_dir), DAY, "test")
    path = alerts.path(settings, DAY)
    rows = duckdb.connect().execute(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY alert_id").fetchall()
    cols = [c for c in alerts.COLUMNS]
    a1, a2 = (dict(zip(cols, r)) for r in rows)
    assert a1["effect"] == "DETOUR" and a1["routes"] == ["R901"]
    assert (a1["first_seen"], a1["last_seen"]) == (O + 7 * H, O + 8 * H)
    assert a1["periods"] == [{"start": O + 7 * H + 1800, "end": O + 10 * H}]
    assert a1["header"] == "Route 901 on detour" and a1["description"] == "Stops E202 and E203 are closed."
    assert a2["effect"] == "SIGNIFICANT_DELAYS" and a2["periods"] == []
    m = events.read_manifest(settings, DAY)
    assert any("/alerts/" in p["path"] for p in m["packs"])

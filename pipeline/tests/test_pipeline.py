from datetime import UTC, datetime, timedelta

from conftest import DAY, after
from wbot_collector import layout

from wbot_pipeline import events, pipeline, static, timeutil
from wbot_pipeline.config import load_settings


def test_plan_trailing_window_and_rebuild(settings, feed):
    feed.write()
    versions = static.list_versions(settings.static_dir)
    now = after(DAY + timedelta(days=4))         # local date is DAY + 5, so the last candidate is DAY + 4
    todo = pipeline.plan(settings, versions, now)
    assert [(e.date, e.process) for e in todo] == [(DAY + timedelta(days=i), True) for i in range(5)]
    pipeline.run(settings, now=now, do_publish=False, generated_at="2026-10-11T12:00:00Z")
    # Next night: only the trailing window (and anything that changed) is redone.
    todo = pipeline.plan(settings, versions, now + 86400)
    assert [(e.date, e.reason) for e in todo] == [(DAY + timedelta(days=3), "recent"),
                                                  (DAY + timedelta(days=4), "recent"), (DAY + timedelta(days=5), "new")]
    assert {e.reason for e in pipeline.plan(settings, versions, now, rebuild=True)} == {"rebuild"}


def test_methodology_version_change_reprocesses_everything(settings, feed):
    feed.write()
    now = after(DAY)
    pipeline.run(settings, now=now, do_publish=False, generated_at="2026-10-07T12:00:00Z")
    versions = static.list_versions(settings.static_dir)
    changed = load_settings({}, data_dir=settings.data_dir, derived_dir=settings.derived_dir,
                            first_service_date=DAY, methodology_version="2")
    assert [(e.date, e.reason) for e in pipeline.plan(changed, versions, now)] == [(DAY, "old_version")]


def test_settings_from_environment(tmp_path):
    s = load_settings({"WBOT_DATA_DIR": str(tmp_path), "WBOT_PIPELINE_TRIP_COVERAGE_MIN": "0.6",
                       "WBOT_PIPELINE_MIN_SAMPLE": "40", "WBOT_PIPELINE_GAP_TOLERANCE_S": "600",
                       "WBOT_PIPELINE_PUBLISH": "1", "WBOT_PIPELINE_PROVISIONAL": "false",
                       "WBOT_PIPELINE_METHODOLOGY_CHANGES": "2026-11-01:2"})
    assert (s.trip_coverage_min, s.min_sample, s.gap_tolerance_s) == (0.6, 40, 600)
    assert s.publish and not s.provisional
    assert s.methodology_changes == (("2026-11-01", "2"),)
    assert s.derived_dir == tmp_path / "derived"
    assert load_settings({}).publish is False


def test_pipeline_version_is_a_commit():
    v = pipeline.pipeline_version()
    assert v and (len(v.split("+")[0]) == 12 or v[0].isdigit())


def test_service_dates_start_at_first_service_date(settings):
    dates = pipeline.candidate_dates(settings, timeutil.origin(DAY) + 3600)
    assert dates == []    # DAY itself isn't over yet


def test_date_waits_while_its_hours_are_still_spooled(settings, feed):
    feed.write()
    versions = static.list_versions(settings.static_dir)
    hours = events.hours_read(settings, versions, DAY)
    spool = layout.spool_hour_dir(settings.spool_dir, events.FEED, datetime.fromtimestamp(hours[-1], UTC))
    spool.mkdir(parents=True)
    (spool / "x.pb").write_bytes(b"")
    result = pipeline.run(settings, now=after(DAY), do_publish=False, generated_at="2026-10-07T12:00:00Z")
    assert result.processed == {DAY.isoformat(): "not_ready"}
    assert events.read_manifest(settings, DAY) is None

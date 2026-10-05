from datetime import timedelta

from conftest import DAY, after

from wbot_pipeline import pipeline, static, timeutil
from wbot_pipeline.config import load_settings


def test_plan_trailing_window_and_rebuild(settings, feed):
    feed.write()
    versions = static.list_versions(settings.static_dir)
    now = after(DAY + timedelta(days=4))         # local date is DAY + 5, so the last candidate is DAY + 4
    todo = pipeline.plan(settings, versions, now)
    assert [d for d, _ in todo] == [DAY + timedelta(days=i) for i in range(5)]
    pipeline.run(settings, now=now, do_publish=False, generated_at="2026-10-11T12:00:00Z")
    # Next night: only the trailing window (and anything that changed) is redone.
    todo = pipeline.plan(settings, versions, now + 86400)
    assert todo == [(DAY + timedelta(days=3), "trailing window"), (DAY + timedelta(days=4), "trailing window"),
                    (DAY + timedelta(days=5), "new")]
    assert {r for _, r in pipeline.plan(settings, versions, now, rebuild=True)} == {"rebuild"}


def test_methodology_version_change_reprocesses_everything(settings, feed):
    feed.write()
    now = after(DAY)
    pipeline.run(settings, now=now, do_publish=False, generated_at="2026-10-07T12:00:00Z")
    versions = static.list_versions(settings.static_dir)
    changed = load_settings({}, data_dir=settings.data_dir, derived_dir=settings.derived_dir,
                            first_service_date=DAY, methodology_version="2")
    assert pipeline.plan(changed, versions, now) == [(DAY, "methodology version")]


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

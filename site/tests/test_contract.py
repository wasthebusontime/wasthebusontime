import json
import shutil

import pytest

from wbot_site import contract
from wbot_site.build import SAMPLE_DIR
from wbot_site.contract import ContractError


@pytest.fixture
def stats(tmp_path):
    out = tmp_path / "stats"
    shutil.copytree(SAMPLE_DIR, out)
    return out


def change(stats, name, fn):
    path = stats / "site" / name
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def first_route(stats):
    return json.loads((stats / "site" / "system.json").read_text(encoding="utf-8"))["routes"][0]["slug"]


def test_the_sample_follows_the_contract():
    contract.validate(SAMPLE_DIR)
    assert contract.map_periods(SAMPLE_DIR)[0] == "all"


def test_count_names():
    assert contract.count_key("on_time") == "on_time"
    assert contract.count_key("on_time", "alt") == "on_time_alt"
    assert contract.COUNTS == ("n", "early", "on_time", "late", "early_alt", "on_time_alt", "late_alt")
    assert set(contract.MAP_CELL) <= set(contract.COUNTS)


def summary(doc):
    return doc["scopes"]["all_stops"]["summary"]


@pytest.mark.parametrize("name, fn, message", [
    ("system.json", lambda d: d.update(schema=2), "system.json: schema 2, expected schema 1"),
    ("meta.json", lambda d: d.pop("windows"), "meta.json: missing windows"),
    ("meta.json", lambda d: d["windows"].pop("alt"), "meta.json: windows: missing alt"),
    ("system.json", lambda d: d["scopes"].pop("all_stops"), "system.json: scopes: missing all_stops"),
    ("system.json", lambda d: d["scopes"]["timepoints"].pop("monthly"), "scopes.timepoints: missing monthly"),
    ("system.json", lambda d: d["scopes"]["timepoints"]["by_daytype"].pop("sunday"), "by_daytype: missing sunday"),
    ("system.json", lambda d: summary(d).pop("late_alt"), "scopes.all_stops.summary: missing late_alt"),
    ("system.json", lambda d: summary(d).update(on_time=summary(d)["on_time"] + 1),
     "summary: headline window counts add up to"),
    ("system.json", lambda d: summary(d).update(late_alt=summary(d)["late_alt"] + 1),
     "summary: alt window counts add up to"),
    ("system.json", lambda d: summary(d).update(n=1.5), "summary: n is 1.5, expected a count"),
    ("system.json", lambda d: summary(d)["hist"].update(over=summary(d)["hist"]["over"] + 1),
     r"summary.hist: counts don't add up"),
    ("system.json", lambda d: d["notices"].append({"code": "data_loss", "start": "x", "end": "y"}),
     r"system.json: notices\[\d+\] \(data_loss\): missing cause"),
    ("system.json", lambda d: d["notices"].append({"date": "2026-10-04"}), r"notices\[\d+\]: missing code"),
    ("system.json", lambda d: d["notices"].append({"code": "detour", "start": "x"}),
     r"notices\[\d+\] \(detour\): missing end"),
    ("quality.json", lambda d: d.update(detours=[{"route": "1", "short_name": "1", "start": "x", "end": "y", "trips": 1}]),
     r"quality.json: detours\[0\]: missing header"),
    ("stops.json", lambda d: d["stops"][0].pop("routes"), r"stops.json: stops\[0\]: missing routes"),
    ("stops.json", lambda d: d["stops"].pop(), r"all-all.json: timepoints has \d+ stops, stops.json has"),
    ("map/all/weekday-midday.json", lambda d: d["all_stops"].pop(), "weekday-midday.json: all_stops has"),
    ("map/all/all-all.json", lambda d: d["all_stops"].__setitem__(0, [1, 0, 1, 0]), r"all_stops\[0\] is \[1, 0, 1, 0\]"),
    ("map/all/all-all.json", lambda d: d["all_stops"].__setitem__(0, None), r"all_stops\[0\] is None"),
])
def test_each_problem_is_reported(stats, name, fn, message):
    change(stats, name, fn)
    with pytest.raises(ContractError, match=message):
        contract.validate(stats)


def test_route_and_stop_files_are_checked(stats):
    slug = first_route(stats)
    change(stats, f"routes/{slug}.json", lambda d: d["scopes"]["all_stops"]["by_direction"][0].pop("headsign"))
    with pytest.raises(ContractError, match=rf"routes/{slug}.json: scopes.all_stops.by_direction\[0\]: missing headsign"):
        contract.validate(stats)


def test_missing_files_are_reported(stats):
    slug = first_route(stats)
    (stats / "site" / "routes" / f"{slug}.json").unlink()
    with pytest.raises(ContractError, match=f"missing site/routes/{slug}.json"):
        contract.validate(stats)
    shutil.rmtree(stats / "site")
    with pytest.raises(ContractError, match="missing site/meta.json"):
        contract.validate(stats)


def test_a_missing_map_preset_is_reported(stats):
    (stats / "site" / "map" / "all" / "sunday-evening.json").unlink()
    with pytest.raises(ContractError, match="missing site/map/all/sunday-evening.json"):
        contract.validate(stats)


def test_what_the_format_allows(stats):
    # Codes the site doesn't know yet, a stop that is no timepoint, and a map without stop positions.
    change(stats, "system.json", lambda d: d["notices"].append({"code": "brand_new_code"}))
    change(stats, "quality.json", lambda d: d.pop("detours", None))   # stats from before detours
    change(stats, "map/all/all-all.json", lambda d: d["timepoints"].__setitem__(0, None))
    slug = first_route(stats)
    change(stats, f"routes/{slug}.json", lambda d: d["stops"][0]["stops"][0].update(timepoints=None))
    contract.validate(stats)
    change(stats, "stops.json", lambda d: d["stops"][0].pop("lat"))
    assert contract.map_periods(stats) == []
    shutil.rmtree(stats / "site" / "map")
    contract.validate(stats)


MIDDAY = "presets/weekday-midday"


def bump_scheduled(doc):
    doc["scheduled"]["all_stops"] += 1


def bump_row(doc):
    bump_scheduled(doc["routes"][0])


@pytest.mark.parametrize("change_to, message", [
    ([(f"{MIDDAY}/system.json", lambda d: d["preset"].update(band="evening"))],
     rf"{MIDDAY}/system.json: preset is .*, expected .*'midday'.* from its folder"),
    ([(f"{MIDDAY}/system.json", lambda d: d["scheduled"].update(all_stops=0))],
     rf"{MIDDAY}/system.json: scheduled.all_stops is 0; a preset with no scheduled departures has no file"),
    ([(f"{MIDDAY}/routes/901.json", lambda d: d["scopes"]["all_stops"]["summary"].pop("late"))],
     rf"{MIDDAY}/routes/901.json: scopes.all_stops.summary: missing late"),
    ([(f"{MIDDAY}/routes/901.json", bump_scheduled)],
     rf"{MIDDAY}/routes/901.json: route or scheduled differ from its row in {MIDDAY}/system.json"),
    ([(f"{MIDDAY}/routes/901.json", bump_scheduled), (f"{MIDDAY}/system.json", bump_row)],
     r"presets/weekday-all/routes/901.json: scheduled.all_stops is (\d+), but presets/weekday-\*/routes/901.json "
     r"add up to (?!\1)\d+"),
    ([(f"{MIDDAY}/system.json", bump_scheduled)],
     r"presets/weekday-all/system.json: scheduled.all_stops is \d+, but presets/weekday-\*/system.json add up to"),
])
def test_each_page_preset_problem_is_reported(stats, change_to, message):
    for name, fn in change_to:
        change(stats, name, fn)
    with pytest.raises(ContractError, match=message):
        contract.validate(stats)


def test_page_preset_files_are_checked(stats):
    presets = stats / "site" / "presets"
    (presets / "weekday-midday" / "routes" / "902.json").unlink()
    with pytest.raises(ContractError, match=f"missing site/{MIDDAY}/routes/902.json"):
        contract.validate(stats)
    change(stats, f"{MIDDAY}/system.json", lambda d: d["routes"].pop(1))
    with pytest.raises(ContractError, match=r"presets/weekday-all/routes/902.json: scheduled.timepoints is \d+, "
                                            r"but presets/weekday-\*/routes/902.json add up to"):
        contract.validate(stats)   # with its row gone too, the bands no longer add up
    shutil.copy(presets / "weekday-all" / "routes" / "901.json", presets / "weekday-midday" / "routes" / "999.json")
    with pytest.raises(ContractError, match=f"{MIDDAY}/routes/999.json: route has no row in {MIDDAY}/system.json"):
        contract.validate(stats)
    (presets / "weekday-late").mkdir()
    with pytest.raises(ContractError, match="presets/weekday-late: not a page preset"):
        contract.validate(stats)


def test_page_presets_are_optional(stats):
    assert len(contract.PRESETS) == 23 and "all-all" not in contract.PRESETS and "weekday-am_peak" in contract.PRESETS
    shutil.rmtree(stats / "site" / "presets")
    contract.validate(stats)
    # A preset with no service has no folder, and that is valid: the sample has no early service.
    assert not (SAMPLE_DIR / "site" / "presets" / "weekday-early").exists()
    contract.validate(SAMPLE_DIR)

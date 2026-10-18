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
    change(stats, "map/all/all-all.json", lambda d: d["timepoints"].__setitem__(0, None))
    slug = first_route(stats)
    change(stats, f"routes/{slug}.json", lambda d: d["stops"][0]["stops"][0].update(timepoints=None))
    contract.validate(stats)
    change(stats, "stops.json", lambda d: d["stops"][0].pop("lat"))
    assert contract.map_periods(stats) == []
    shutil.rmtree(stats / "site" / "map")
    contract.validate(stats)

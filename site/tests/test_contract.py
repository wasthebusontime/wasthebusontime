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


TRIPS_HEADER = ",".join(contract.TRIPS_COLUMNS)


@pytest.mark.parametrize(("name", "text", "message"), [
    ("trips_2026-10.csv", TRIPS_HEADER + "\n2026-10-06,13,0,t1,07:00:00,30,250,B1,t0,240,220,yes\n", None),
    ("trips_2026-10.csv", TRIPS_HEADER + "\n2026-10-06,13,0,t1,07:00:00,,,,,,,\n", None),
    ("trips-2026-10.csv", TRIPS_HEADER + "\n", "expected trips_YYYY-MM.csv"),
    ("trips_2026-10.csv", "date,route\n", "columns aren't"),
    ("trips_2026-10.csv", TRIPS_HEADER + "\n2026-11-01,13,0,t1,07:00:00,,,,,,,\n", "line 2 is malformed"),
    ("trips_2026-10.csv", TRIPS_HEADER + "\n2026-10-06,13,0,t1,07:00:00,,,,,,,maybe\n", "line 2 is malformed"),
])
def test_trips_downloads_are_checked_when_present(stats, name, text, message):
    (stats / "trips").mkdir()
    (stats / "trips" / name).write_text(text, encoding="utf-8")
    if message is None:
        contract.validate(stats)
    else:
        with pytest.raises(ContractError, match=message):
            contract.validate(stats)


# Patterns (site/patterns.json and csv/patterns.csv).

def patterns_doc(stats):
    return json.loads((stats / "site" / contract.PATTERNS_FILE).read_text(encoding="utf-8"))


def general(doc):
    return next(p for p in doc["patterns"] if p["preset"] is None)


def test_the_sample_has_patterns():
    doc = patterns_doc(SAMPLE_DIR)
    kinds = {(p["route"], p["preset"] is None) for p in doc["patterns"]}
    assert any(g for _, g in kinds) and any(not g for _, g in kinds)
    assert doc["too_little_data"] and set(doc["routes"]) | set(doc["too_little_data"]) < \
        {row["slug"] for row in json.loads((SAMPLE_DIR / "site" / "system.json").read_text())["routes"]}


def test_pattern_filters():
    assert contract.pattern_filters(None) == ["all-all", *contract.PRESETS]
    assert contract.pattern_filters({"daytype": "all", "band": "pm_peak"}) == [
        "all-all", "all-pm_peak", "weekday-pm_peak", "saturday-pm_peak", "sunday-pm_peak"]
    assert contract.pattern_filters({"daytype": "weekday", "band": "pm_peak"}) == ["all-all", "weekday-pm_peak"]
    assert contract.pattern_filters({"daytype": "saturday", "band": "all"}) == [
        "all-all", *(f"saturday-{b}" for b in contract.MAP_BANDS)]


def set_share(doc, share):
    general(doc).update(share=share)


@pytest.mark.parametrize("fn, message", [
    (lambda d: d.pop("thresholds"), "patterns.json: missing thresholds"),
    (lambda d: d["period"].update(start="2026-10-05"), "patterns.json: period"),
    (lambda d: general(d).pop("rank"), r"patterns.json: patterns\[\d+\]: missing rank"),
    (lambda d: general(d).update(kind="weather"), "kind 'weather' isn't one of"),
    (lambda d: d["patterns"].append(dict(general(d))), "appears twice"),
    (lambda d: general(d).update(route="nope"), "route 'nope' is not in system.json"),
    (lambda d: general(d).update(stops=["nope"]), "aren't stops in stops.json"),
    (lambda d: general(d).update(preset={"daytype": "all", "band": "all"}), "isn't a page preset"),
    (lambda d: general(d).update(preset={"daytype": "weekday", "band": "rush"}), "isn't a page preset"),
    (lambda d: general(d).update(trips=-1), "trips is -1, expected a count"),
    (lambda d: general(d).update(matched=general(d)["late"] + 1), "matched .* of late"),
    (lambda d: set_share(d, 0.99), "share 0.99 isn't matched / late"),
    (lambda d: general(d).update(rank=7), "has ranks"),
    (lambda d: d["routes"][general(d)["route"]].update({"all-all": 99}), "patterns.json: routes."),
    (lambda d: d["stops"].pop(general(d)["stops"][0]), "patterns.json: stops."),
    (lambda d: d["too_little_data"].append(general(d)["route"]), "too_little_data lists"),
    (lambda d: general(d).pop("previous"), r"patterns\[\d+\]: missing previous"),
    (lambda d: general(d).update(previous=[{"route": "nope", "direction_id": 0, "matched": 1}]),
     "previous route 'nope' is not in system.json"),
    (lambda d: general(d).update(previous=[{"route": general(d)["route"], "direction_id": 2, "matched": 1}]),
     "previous direction_id 2"),
    (lambda d: general(d).update(previous=[{"route": general(d)["route"], "direction_id": 0,
                                            "matched": general(d)["matched"] + 1}]), "previous adds up to"),
    (lambda d: general(d).update(previous=[{"route": general(d)["route"], "direction_id": 0, "matched": 1},
                                           {"route": general(d)["route"], "direction_id": 1, "matched": 2}]),
     "previous isn't largest first"),
])
def test_each_pattern_problem_is_reported(stats, fn, message):
    change(stats, contract.PATTERNS_FILE, fn)
    with pytest.raises(ContractError, match=message):
        contract.validate(stats)


def test_patterns_csv_matches_the_json(stats):
    path = stats / "csv" / contract.PATTERNS_CSV
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="rows don't match"):
        contract.validate(stats)
    path.write_text("id,kind\n", encoding="utf-8")
    with pytest.raises(ContractError, match="columns aren't"):
        contract.validate(stats)
    path.unlink()
    with pytest.raises(ContractError, match="missing csv/patterns.csv"):
        contract.validate(stats)


def test_patterns_are_optional(stats):
    (stats / "site" / contract.PATTERNS_FILE).unlink()
    (stats / "csv" / contract.PATTERNS_CSV).unlink()
    contract.validate(stats)


# End-of-line arrivals: in time for the next trip, per route stop and per stop (optional keys).

def last_stop(doc):
    return doc["stops"][0]["stops"][-1]


@pytest.mark.parametrize("name, fn, message", [
    ("system.json", lambda d: d["end_of_line"]["next"].update(in_time=d["end_of_line"]["next"]["n"] + 1),
     r"system.json: end_of_line.next: in_time \d+ is more than n"),
    ("system.json", lambda d: d["end_of_line"]["next"].update(n=d["end_of_line"]["n"] + 1),
     r"system.json: end_of_line.next: n \d+ is more than the \d+ arrivals"),
    ("system.json", lambda d: d["end_of_line"]["next"].pop("spare_p50"), "end_of_line.next: missing spare_p50"),
    ("system.json", lambda d: d["end_of_line"].pop("sources"), "system.json: end_of_line: missing sources"),
    ("system.json", lambda d: d["end_of_line"].update(late=d["end_of_line"]["n"] + 1),
     r"system.json: end_of_line: late \d+ is more than the \d+ arrivals"),
    ("routes/905.json", lambda d: last_stop(d).update(arrivals=[]), r"stops\[0\].stops\[\d+\].arrivals: expected an object"),
    ("routes/905.json", lambda d: last_stop(d)["arrivals"]["next"].update(in_time=-1), r"arrivals.next: in_time is -1"),
    ("presets/weekday-am_peak/routes/905.json", lambda d: last_stop(d)["arrivals"].pop("n"), r"arrivals: missing n"),
    ("stops/E133.json", lambda d: d["end_of_line"]["by_route"][0].pop("slug"), r"end_of_line.by_route\[0\]: missing slug"),
    ("stops/E133.json", lambda d: d["end_of_line"].pop("by_route"), r"E133.json: end_of_line: missing by_route"),
])
def test_each_end_of_line_problem_is_reported(stats, name, fn, message):
    change(stats, name, fn)
    with pytest.raises(ContractError, match=message):
        contract.validate(stats)


def strip_arrivals(doc):
    doc.get("end_of_line", {}) and doc["end_of_line"].pop("next", None)
    doc.get("end_of_line", {}) and doc["end_of_line"].pop("late", None)
    for d in doc.get("stops", []):
        for s in d["stops"]:
            s.pop("arrivals", None)


def test_arrivals_and_the_next_trip_are_optional(stats):
    site = stats / "site"
    for path in [site / "system.json", *site.glob("routes/*.json"), *site.glob("presets/*/routes/*.json")]:
        change(stats, path.relative_to(site).as_posix(), strip_arrivals)
    for path in site.glob("stops/*.json"):
        change(stats, path.relative_to(site).as_posix(), lambda d: d.pop("end_of_line", None))
    contract.validate(stats)


def test_the_sample_has_arrivals_where_trips_end():
    route = json.loads((SAMPLE_DIR / "site" / "routes" / "905.json").read_text(encoding="utf-8"))
    assert all(d["stops"][-1]["arrivals"]["next"]["n"] > 0 for d in route["stops"])
    stop = json.loads((SAMPLE_DIR / "site" / "stops" / "E133.json").read_text(encoding="utf-8"))
    assert stop["end_of_line"]["next"]["in_time"] > 0

import json

from wbot_pipeline import gaps, timeutil

T0 = timeutil.parse_iso_utc("2026-10-06T15:00:00Z")
SERVICE = [(T0, T0 + 3600)]


def fetch(ts, status=200, error=None, entities=5, data_ts=None):
    return {"ts": ts, "status": status, "error": error, "entities": entities,
            "data_ts": ts - 3 if data_ts is None else data_ts}


def polls(start, end, **kw):
    return [fetch(t, **kw) for t in range(start, end, 30)]


def test_no_gaps_when_every_poll_succeeds():
    assert gaps.find_gaps(polls(T0 - 300, T0 + 4000), SERVICE) == []


def test_missing_log_lines_are_a_collector_gap():
    rows = polls(T0 - 300, T0 + 600) + polls(T0 + 1800, T0 + 4000)
    (g,) = gaps.find_gaps(rows, SERVICE)
    assert g["start"] == T0 + 600 and g["end"] == T0 + 1800 and g["cause"] == "collector"
    assert gaps.lost_seconds([g], SERVICE) == 1200


def test_http_errors_and_stale_data_are_feed_gaps():
    rows = polls(T0 - 300, T0 + 600) + polls(T0 + 600, T0 + 900, status=503, entities=None) + \
        polls(T0 + 900, T0 + 4000)
    assert [g["cause"] for g in gaps.find_gaps(rows, SERVICE)] == ["feed"]
    stale = [fetch(t, data_ts=T0) for t in range(T0 + 600, T0 + 1500, 30)]
    rows = polls(T0 - 300, T0 + 600) + stale + polls(T0 + 1500, T0 + 4000)
    (g,) = gaps.find_gaps(rows, SERVICE)
    assert g["cause"] == "feed" and g["start"] == T0 + 630   # the first poll more than 10 min stale


def test_connection_errors_are_ours_and_timeouts_unknown():
    for error, cause in (("ConnectError: [Errno 101] Network is unreachable", "collector"),
                         ("ReadTimeout: timed out", "unknown")):
        rows = polls(T0 - 300, T0 + 600) + polls(T0 + 600, T0 + 900, status=None, entities=None, error=error) + \
            polls(T0 + 900, T0 + 4000)
        assert [g["cause"] for g in gaps.find_gaps(rows, SERVICE)] == [cause]


def test_outside_service_nothing_counts():
    # Overnight: empty feeds and old timestamps are normal, and no fetches at all lose nothing.
    rows = polls(T0 - 7200, T0 - 3600, entities=0, data_ts=0) + polls(T0 - 300, T0 + 4000)
    assert gaps.find_gaps(rows, SERVICE) == []
    assert gaps.find_gaps([], []) == []


def test_whole_service_missing():
    (g,) = gaps.find_gaps([], SERVICE)
    assert (g["start"], g["end"], g["cause"]) == (T0, T0 + 3600, "collector")


def test_merge_intervals():
    assert gaps.merge([(5, 9), (1, 3), (2, 4), (9, 10)]) == [(1, 4), (5, 10)]


def test_overrides(tmp_path):
    path = tmp_path / "causes.toml"
    path.write_text('[[gap]]\nstart = "2026-10-06T15:05:00Z"\nend = "2026-10-06T15:15:00Z"\n'
                    'cause = "collector"\nnote = "planned test"\n')
    found = [{"start": T0 + 600, "end": T0 + 1800, "cause": "unknown"}]
    (g,) = gaps.apply_overrides(found, gaps.load_overrides(path))
    assert g["cause"] == "collector" and g["note"] == "planned test"
    assert gaps.load_overrides(tmp_path / "missing.toml") == []


def test_read_fetches_keeps_only_tripupdates(tmp_path):
    lines = [{"fetched_at": "2026-10-06T15:00:00.000Z", "feed": "tripupdates", "status": 200},
             {"fetched_at": "2026-10-06T15:00:00.463Z", "feed": "vehiclepositions", "status": 200}]
    (tmp_path / "2026-10-06.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    rows = gaps.read_fetches(tmp_path, T0, T0 + 60)
    assert len(rows) == 1 and rows[0]["ts"] == T0

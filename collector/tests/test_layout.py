from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from wbot_collector import layout

TS = datetime(2026, 10, 4, 3, 24, 0, tzinfo=UTC)


def test_paths_follow_the_fixed_layout():
    # The layout is fixed (the 2026-10-03 storage decisions); these pin it.
    assert layout.spool_path(Path("spool"), "tripupdates", TS).as_posix() == \
        "spool/tripupdates/2026-10-04/03/tripupdates-20261004T032400Z.pb"
    assert layout.spool_path(Path("spool"), "wsdot_alerts", TS, ".json", n=2).name == \
        "wsdot_alerts-20261004T032400Z-2.json"
    assert layout.pack_path(Path("archive"), "tripupdates", TS).as_posix() == \
        "archive/tripupdates/2026/10/04/tripupdates-2026-10-04T03.tar.zst"
    assert layout.pack_path(Path("archive"), "tripupdates", TS, 1).name == "tripupdates-2026-10-04T03.1.tar.zst"
    assert layout.log_path(Path("log"), date(2026, 10, 4)).as_posix() == "log/2026-10-04.jsonl"
    assert layout.static_stem("2026 fall/1", "9de242c4ffff") == "2026_fall_1_9de242c4"
    assert layout.static_stem("", "9de242c4ffff") == "noversion_9de242c4"
    assert layout.sidecar_path(layout.static_zip(Path("static"), "v_1")).as_posix() == "static/v_1.json"


def test_snapshot_time_from_file_name():
    assert layout.snapshot_time(layout.spool_path(Path(), "tripupdates", TS).name) == int(TS.timestamp())
    assert layout.snapshot_time("tripupdates-20261004T032400Z-1.pb") == int(TS.timestamp())
    assert layout.snapshot_time("other.pb") is None


def test_spool_hour_from_directory_names():
    d = layout.spool_hour_dir(Path("spool"), "tripupdates", TS)
    assert layout.spool_hour(d.parent, d) == datetime(2026, 10, 4, 3, tzinfo=UTC)
    assert layout.spool_hour(Path("2026-10-04"), Path("junk")) is None


def test_packs_for_hour_in_order(tmp_path):
    for n in (2, 0, 1):
        p = layout.pack_path(tmp_path, "tripupdates", TS, n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    # Another hour's pack in the same folder is not included.
    layout.pack_path(tmp_path, "tripupdates", datetime(2026, 10, 4, 13, tzinfo=UTC)).write_bytes(b"")
    assert [p.name for p in layout.packs_for_hour(tmp_path, "tripupdates", TS)] == [
        "tripupdates-2026-10-04T03.tar.zst", "tripupdates-2026-10-04T03.1.tar.zst", "tripupdates-2026-10-04T03.2.tar.zst"]


def test_fetch_record_has_every_field_in_order():
    r = layout.fetch_record(TS, "tripupdates", "https://example.invalid/tu", status=200)
    assert list(r) == list(layout.FetchRecord.__annotations__)
    assert r["fetched_at"] == "2026-10-04T03:24:00.000Z"
    assert r["status"] == 200 and r["error"] is None
    with pytest.raises(TypeError, match="statuss"):
        layout.fetch_record(TS, "tripupdates", "u", statuss=200)

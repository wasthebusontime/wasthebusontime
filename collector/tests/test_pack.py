import hashlib
from datetime import UTC, datetime

import pytest

from conftest import make_feed
from wbot_collector import pack
from wbot_collector.pack import archive_hashes, pack_completed

HOUR_DIR = ("tripupdates", "2026-10-04", "02")
ARCHIVE = "archive/tripupdates/2026/10/04/tripupdates-2026-10-04T02.tar.zst"


def spool(settings, *parts_and_files):
    hour_dir = settings.spool_dir.joinpath(*HOUR_DIR)
    hour_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, data in parts_and_files:
        (hour_dir / name).write_bytes(data)
        out[name] = hashlib.sha256(data).hexdigest()
    return hour_dir, out


def snapshots(n):
    return [(f"tripupdates-20261004T02{i:02d}00Z.pb", make_feed(1_791_000_000 + 30 * i, 50)) for i in range(n)]


def test_packs_completed_hour_and_verifies(settings):
    hour_dir, hashes = spool(settings, *snapshots(10))
    n = pack_completed(settings, now=datetime(2026, 10, 4, 3, 5, tzinfo=UTC))

    assert n == 10
    archive = settings.data_dir / ARCHIVE
    assert archive_hashes(archive) == hashes
    assert not hour_dir.exists()
    assert not hour_dir.parent.exists()  # empty date dir removed


def test_skips_current_hour_and_grace_period(settings):
    hour_dir, _ = spool(settings, *snapshots(2))
    assert pack_completed(settings, now=datetime(2026, 10, 4, 2, 59, tzinfo=UTC)) == 0
    assert pack_completed(settings, now=datetime(2026, 10, 4, 3, 1, tzinfo=UTC)) == 0
    assert hour_dir.exists()


def test_late_files_go_to_numbered_archive(settings):
    later = datetime(2026, 10, 4, 3, 5, tzinfo=UTC)
    spool(settings, *snapshots(3))
    pack_completed(settings, now=later)
    _, late = spool(settings, ("tripupdates-20261004T025959Z.pb", b"late"))
    assert pack_completed(settings, now=later) == 1
    second = settings.data_dir / ARCHIVE.replace(".tar.zst", ".1.tar.zst")
    assert archive_hashes(second) == late


def test_interrupted_run_does_not_duplicate(settings):
    later = datetime(2026, 10, 4, 3, 5, tzinfo=UTC)
    files = snapshots(3)
    spool(settings, *files)
    pack_completed(settings, now=later)
    # Simulate a crash after archiving but before the spool was deleted.
    hour_dir, _ = spool(settings, *files)
    assert pack_completed(settings, now=later) == 0
    assert not hour_dir.exists()
    assert not (settings.data_dir / ARCHIVE.replace(".tar.zst", ".1.tar.zst")).exists()


def test_leftover_tmp_files_are_discarded(settings):
    hour_dir, hashes = spool(settings, *snapshots(2))
    (hour_dir / ".tmp-partial.pb").write_bytes(b"half")
    pack_completed(settings, now=datetime(2026, 10, 4, 3, 5, tzinfo=UTC))
    assert archive_hashes(settings.data_dir / ARCHIVE) == hashes


def test_verify_failure_keeps_spool(settings, monkeypatch):
    hour_dir, _ = spool(settings, *snapshots(2))
    monkeypatch.setattr(pack, "archive_hashes", lambda path: {})
    with pytest.raises(pack.VerifyError):
        pack_completed(settings, now=datetime(2026, 10, 4, 3, 5, tzinfo=UTC))
    assert len(list(hour_dir.iterdir())) == 2
    assert not (settings.data_dir / ARCHIVE).exists()


def test_archive_has_no_local_user_names(settings):
    import tarfile

    import zstandard

    spool(settings, *snapshots(1))
    pack_completed(settings, now=datetime(2026, 10, 4, 3, 5, tzinfo=UTC))
    with open(settings.data_dir / ARCHIVE, "rb") as f:
        with tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(f), mode="r|") as tar:
            for m in tar:
                assert (m.uname, m.gname, m.uid, m.gid) == ("", "", 0, 0)

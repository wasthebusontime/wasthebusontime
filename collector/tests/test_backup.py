from wbot_collector import backup
from wbot_collector.config import Settings


def test_backup_includes_existing_dirs_including_derived(tmp_path):
    settings = Settings(data_dir=tmp_path, b2_bucket="bucket")
    for d in ("archive", "static", "log", "derived", "spool"):
        (tmp_path / d).mkdir()
    cmds = backup.rclone_commands(settings)
    targets = [c[3] for c in cmds]
    assert targets == ["b2:bucket/archive", "b2:bucket/static", "b2:bucket/log", "b2:bucket/derived"]
    assert all(c[1] == "copy" for c in cmds)     # never sync: local deletions never reach the backup
    assert "tmp/**" in cmds[-1] and "out*/**" in cmds[-1]


def test_backup_skips_missing_dirs(tmp_path):
    (tmp_path / "archive").mkdir()
    assert [c[3] for c in backup.rclone_commands(Settings(data_dir=tmp_path, b2_bucket="b"))] == ["b2:b/archive"]

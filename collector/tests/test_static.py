import hashlib
import io
import json
import zipfile

import httpx
import pytest

from wbot_collector.static_gtfs import check


def make_zip(version: str, extra: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "feed_info.txt",
            "feed_publisher_name,feed_version,feed_start_date,feed_end_date\n"
            f"Example,{version},20260927,20270102\n",
        )
        z.writestr("agency.txt", "agency_id,agency_name\nA,Example Agency\n" + extra)
    return buf.getvalue()


class FakeServer:
    """Serves one zip with an ETag and honors If-None-Match."""

    def __init__(self, content: bytes, etag: str):
        self.content, self.etag = content, etag
        self.requests = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.headers.get("If-None-Match") == self.etag:
            return httpx.Response(304)
        return httpx.Response(200, content=self.content, headers={"ETag": self.etag})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_saves_new_version_then_uses_conditional_get(settings):
    content = make_zip("20260927")
    server = FakeServer(content, '"v1"')

    assert check(settings, server.client(), url="https://example.invalid/gtfs.zip") == "saved"
    zips = list(settings.static_dir.glob("*.zip"))
    assert [z.name for z in zips] == [f"20260927_{hashlib.sha256(content).hexdigest()[:8]}.zip"]
    assert zips[0].read_bytes() == content
    sidecar = json.loads(zips[0].with_suffix(".json").read_text())
    assert sidecar["feed_info"]["feed_end_date"] == "20270102"
    assert sidecar["etag"] == '"v1"'

    assert check(settings, server.client(), url="https://example.invalid/gtfs.zip") == "unchanged"
    assert server.requests[-1].headers["If-None-Match"] == '"v1"'


def test_same_content_new_etag_is_not_saved_twice(settings):
    content = make_zip("20260927")
    check(settings, FakeServer(content, '"v1"').client(), url="https://example.invalid/gtfs.zip")
    assert check(settings, FakeServer(content, '"v2"').client(), url="https://example.invalid/gtfs.zip") == "duplicate"
    assert len(list(settings.static_dir.glob("*.zip"))) == 1


def test_new_schedule_is_kept_alongside_old(settings):
    check(settings, FakeServer(make_zip("20260927"), '"v1"').client(), url="https://example.invalid/gtfs.zip")
    check(settings, FakeServer(make_zip("20270103"), '"v2"').client(), url="https://example.invalid/gtfs.zip")
    names = sorted(z.name.split("_")[0] for z in settings.static_dir.glob("*.zip"))
    assert names == ["20260927", "20270103"]


def test_non_zip_response_is_rejected(settings):
    server = FakeServer(b"<html>maintenance</html>", '"x"')
    with pytest.raises(RuntimeError, match="not a zip"):
        check(settings, server.client(), url="https://example.invalid/gtfs.zip")
    assert not settings.static_dir.exists()
    log = (next(settings.log_dir.glob("*.jsonl"))).read_text()
    assert "not a zip" in log

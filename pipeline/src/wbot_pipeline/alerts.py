"""Service alerts from the archive, and when a detour counts as active.

The agency posts detours as alerts with effect DETOUR. They name routes and times, not
stops or trips, and usually tag one route even when the text names several, so
alert_routes.toml (next to this module) adds the routes the text names.

Writes derived/alerts/YYYY/YYYY-MM-DD.parquet: every alert seen while a service date's
hours were read, one row per alert id.
"""

import tomllib
from datetime import date
from pathlib import Path

import pyarrow as pa
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as R

from .config import Settings

# The collector polls alerts every 5 minutes.
POLL_S = 300
EXTRA_ROUTES = Path(__file__).with_name("alert_routes.toml")
COLUMNS = ("alert_id", "effect", "cause", "routes", "stops", "trips", "periods", "header", "description",
           "first_seen", "last_seen")
SCHEMA = pa.schema([
    ("alert_id", pa.string()), ("effect", pa.string()), ("cause", pa.string()),
    ("routes", pa.list_(pa.string())), ("stops", pa.list_(pa.string())), ("trips", pa.list_(pa.string())),
    ("periods", pa.list_(pa.struct([("start", pa.int64()), ("end", pa.int64())]))),
    ("header", pa.string()), ("description", pa.string()),
    ("first_seen", pa.int64()), ("last_seen", pa.int64()),
])


def path(settings: Settings, d: date) -> Path:
    return settings.derived_dir / "alerts" / f"{d:%Y}" / f"{d.isoformat()}.parquet"


def _text(t: R.TranslatedString) -> str:
    for tr in t.translation:
        if tr.language in ("", "en"):
            return tr.text
    return t.translation[0].text if t.translation else ""


class Alerts:
    """Every alert seen in a run of snapshots: its latest content, first and last time seen."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def add(self, snap: int, data: bytes) -> None:
        msg = R.FeedMessage()
        try:
            msg.ParseFromString(data)
        except DecodeError:
            return
        for e in msg.entity:
            if not e.HasField("alert"):
                continue
            a = e.alert
            row = self.rows.setdefault(e.id, {"alert_id": e.id, "first_seen": snap,
                                              "routes": set(), "stops": set(), "trips": set()})
            row["last_seen"] = snap
            row["effect"] = R.Alert.Effect.Name(a.effect)
            row["cause"] = R.Alert.Cause.Name(a.cause)
            for ie in a.informed_entity:
                if ie.route_id:
                    row["routes"].add(ie.route_id)
                if ie.stop_id:
                    row["stops"].add(ie.stop_id)
                if ie.trip.trip_id:
                    row["trips"].add(ie.trip.trip_id)
            row["periods"] = [{"start": p.start, "end": p.end} for p in a.active_period]
            row["header"] = _text(a.header_text)
            row["description"] = _text(a.description_text)

    def table(self) -> pa.Table:
        rows = [{**r, "routes": sorted(r["routes"]), "stops": sorted(r["stops"]), "trips": sorted(r["trips"])}
                for _, r in sorted(self.rows.items())]
        return pa.Table.from_pylist(rows, schema=SCHEMA)


def windows(periods: list[tuple[int, int]], first_seen: int, last_seen: int) -> list[tuple[int, int]]:
    """When an alert counts as active: its active periods (0 is an open end), cut to the
    time it was in the feed (to the poll after it was last seen). No periods: the whole
    time it was in the feed."""
    shown = (first_seen, last_seen + POLL_S)
    out = []
    for start, end in periods or [(0, 0)]:
        lo, hi = max(start or shown[0], shown[0]), min(end or shown[1], shown[1])
        if lo < hi:
            out.append((lo, hi))
    return out


def load_extra_routes(file: Path = EXTRA_ROUTES) -> dict[str, list[str]]:
    """{alert id: routes to add}. ValueError if an entry is malformed."""
    if not file.exists():
        return {}
    with open(file, "rb") as f:
        doc = tomllib.load(f)
    out = {}
    for i, entry in enumerate(doc.get("alert", [])):
        routes = entry.get("routes")
        if not isinstance(entry.get("id"), str) or not (isinstance(routes, list) and routes
                                                        and all(isinstance(r, str) for r in routes)):
            raise ValueError(f"{file.name}: alert {i}: needs id (text) and routes (a list of route ids)")
        out[entry["id"]] = routes
    return out

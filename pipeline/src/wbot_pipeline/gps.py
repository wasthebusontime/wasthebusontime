"""Vehicle positions from the archive, and a trip's arrival at its last stop from them.

Used only where the next trip in the block can't give the arrival (events.py). The arrival
is the midpoint between the last report more than RADIUS_M from the stop and the first
within it, the two at most MAX_GAP_S apart: the method checked in the October 2026
re-validation (section E1). Reports come about every 30 s, so it is good to about 15 s.
"""

import bisect
import math

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as R

RADIUS_M = 40
MAX_GAP_S = 75


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two (lat, lon) points; flat-earth, fine at a few hundred metres."""
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


class Positions:
    """Every vehicle's reports, (timestamp, lat, lon), without repeats."""

    def __init__(self):
        self._raw: dict[str, set] = {}
        self._sorted: dict[str, list] = {}

    def add(self, data: bytes) -> None:
        msg = R.FeedMessage()
        try:
            msg.ParseFromString(data)
        except DecodeError:
            return
        for e in msg.entity:
            v = e.vehicle
            if e.HasField("vehicle") and v.vehicle.id and v.HasField("position") and v.timestamp:
                self._raw.setdefault(v.vehicle.id, set()).add((v.timestamp, v.position.latitude, v.position.longitude))
        self._sorted.clear()

    def reports(self, vehicle_id: str, start: int, end: int) -> list[tuple[int, float, float]]:
        if vehicle_id not in self._sorted:
            self._sorted[vehicle_id] = sorted(self._raw.get(vehicle_id, ()))
        rows = self._sorted[vehicle_id]
        times = [r[0] for r in rows]
        return rows[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]


def arrival(reports: list[tuple[int, float, float]], stop: tuple[float, float]) -> int | None:
    """When the bus first came within RADIUS_M of the stop, or None."""
    for (t1, la1, lo1), (t2, la2, lo2) in zip(reports, reports[1:]):
        if (t2 - t1 <= MAX_GAP_S and distance_m(stop, (la1, lo1)) > RADIUS_M
                and distance_m(stop, (la2, lo2)) <= RADIUS_M):
            return (t1 + t2) // 2
    return None

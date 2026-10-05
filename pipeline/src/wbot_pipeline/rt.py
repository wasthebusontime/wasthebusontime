"""Reduce a service date's TripUpdates snapshots to one row per (trip, stop_sequence).

The Avail feed keeps each passed stop about 15 minutes, and its times stop changing
once the bus has left. So for each stop we keep the last times seen before it drops
out, plus what the methodology's "observed" rule needs: whether the stop was seen
after its departure while the trip had a vehicle, and the trip's last
trip_update.timestamp. Memory is one small list per stop (about 50k a day).
"""

import logging
from dataclasses import dataclass, field

import pyarrow as pa
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as R

log = logging.getLogger(__name__)

TRIP_SR = {v.number: v.name for v in R.TripDescriptor.ScheduleRelationship.DESCRIPTOR.values}
STOP_SR = {v.number: v.name for v in R.TripUpdate.StopTimeUpdate.ScheduleRelationship.DESCRIPTOR.values}

# Per-stop state, as a list for speed: indexes into it.
LAST, ARR, DEP, SR, TU_TS, AFTER_DEP, AFTER_ARR, STOP_ID, FIRST = range(9)


@dataclass
class Reduced:
    stops: dict = field(default_factory=dict)   # (trip_id, stop_sequence) -> state list
    trips: dict = field(default_factory=dict)   # trip_id -> [trip_sr, route_id, first, last, vehicle_seen]
    snapshots: int = 0
    unreadable: int = 0
    first_snapshot: int | None = None
    last_snapshot: int | None = None

    def add(self, snap: int, data: bytes, service_date: str) -> None:
        msg = R.FeedMessage()
        try:
            msg.ParseFromString(data)
        except DecodeError:
            self.unreadable += 1
            return
        self.snapshots += 1
        if self.first_snapshot is None:
            self.first_snapshot = snap
        self.last_snapshot = snap
        stops, trips = self.stops, self.trips
        for e in msg.entity:
            if not e.HasField("trip_update"):
                continue
            tu = e.trip_update
            if tu.trip.start_date != service_date:
                continue
            trip_id = tu.trip.trip_id
            veh = bool(tu.vehicle.id)
            t = trips.get(trip_id)
            if t is None:
                trips[trip_id] = [tu.trip.schedule_relationship, tu.trip.route_id, snap, snap, veh]
            else:
                t[0] = tu.trip.schedule_relationship
                t[3] = snap
                if veh:
                    t[4] = True
            ts = tu.timestamp
            for s in tu.stop_time_update:
                arr = s.arrival.time or None
                dep = s.departure.time or None
                key = (trip_id, s.stop_sequence)
                v = stops.get(key)
                if v is None:
                    stops[key] = [snap, arr, dep, s.schedule_relationship, ts,
                                  bool(veh and dep and snap > dep), bool(veh and arr and snap > arr), s.stop_id, snap]
                else:
                    v[LAST] = snap
                    v[ARR] = arr
                    v[DEP] = dep
                    v[SR] = s.schedule_relationship
                    v[TU_TS] = ts
                    if veh and dep and snap > dep:
                        v[AFTER_DEP] = True
                    if veh and arr and snap > arr:
                        v[AFTER_ARR] = True
                    if s.stop_id:
                        v[STOP_ID] = s.stop_id

    def stops_table(self) -> pa.Table:
        keys = list(self.stops)
        vals = [self.stops[k] for k in keys]
        return pa.table({
            "trip_id": pa.array([k[0] for k in keys], pa.string()),
            "stop_sequence": pa.array([k[1] for k in keys], pa.int32()),
            "rt_stop_id": pa.array([v[STOP_ID] or None for v in vals], pa.string()),
            "act_arr_ts": pa.array([v[ARR] for v in vals], pa.int64()),
            "act_dep_ts": pa.array([v[DEP] for v in vals], pa.int64()),
            "stop_sr": pa.array([STOP_SR.get(v[SR], str(v[SR])) for v in vals], pa.string()),
            "tu_ts": pa.array([v[TU_TS] or None for v in vals], pa.int64()),
            "after_dep": pa.array([v[AFTER_DEP] for v in vals], pa.bool_()),
            "after_arr": pa.array([v[AFTER_ARR] for v in vals], pa.bool_()),
            "first_seen_ts": pa.array([v[FIRST] for v in vals], pa.int64()),
            "last_seen_ts": pa.array([v[LAST] for v in vals], pa.int64()),
        })

    def trips_table(self) -> pa.Table:
        keys = list(self.trips)
        vals = [self.trips[k] for k in keys]
        return pa.table({
            "trip_id": pa.array(keys, pa.string()),
            "trip_sr": pa.array([TRIP_SR.get(v[0], str(v[0])) for v in vals], pa.string()),
            "rt_route_id": pa.array([v[1] or None for v in vals], pa.string()),
            "first_seen_ts": pa.array([v[2] for v in vals], pa.int64()),
            "last_seen_ts": pa.array([v[3] for v in vals], pa.int64()),
            "vehicle_seen": pa.array([v[4] for v in vals], pa.bool_()),
        })

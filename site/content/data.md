Title: Data

# Data

The statistics on this site can be downloaded as CSV files below. They contain aggregate counts only (for example, how many departures were early, on time or late on a route in a month). One row per bus trip is in the per-trip files described below.

## What's in the files

Every file counts recorded departures. Rows can be added together, for example to combine days into a week.

| File | One row per |
|---|---|
| `system_daily.csv` | day and stop scope |
| `routes_daily.csv` | day, route, direction and stop scope |
| `routes_monthly.csv` | month, route, direction, stop scope and day type |
| `routes_hourly_monthly.csv` | month, route, stop scope, day type and hour |
| `stops_monthly.csv` | month, stop, route and stop scope |
| `terminal_monthly.csv` | month and route, for departures from each trip's first stop |
| `end_of_line_monthly.csv` | month and route, for arrivals at each trip's last stop |
| `quality_daily.csv` | day: trips and timepoint departures scheduled and recorded, and how much of the service time we recorded |
| `data_loss.csv` | break in our recording long enough to lose departures (times in UTC) |
| `detours.csv` | detour Intercity Transit posted in its real-time data, and route: when it was in effect (times in UTC), how many trips ran during it, and the alert's headline |

Columns:

- `scope` is `timepoints` or `all_stops`, the same choice as the toggle on each page. `day_type` is `weekday`, `saturday` or `sunday`. `hour` is the scheduled hour of the day, where 24 and later are trips after midnight that belong to the day before. `direction_id` is 0 or 1.
- `n` is the number of recorded departures. `early`, `on_time` and `late` use 1 min early to 5 min late. `early_alt`, `on_time_alt` and `late_alt` use Intercity Transit's own definition (0 to 5 min late).
- `under`, `m_10` to `m_1`, `m0` to `m19` and `over` count departures by how many minutes late they left: `m0` is 0 to 1 minute late, `m_1` is up to 1 minute early, `under` is more than 10 minutes early and `over` is 20 or more minutes late.
- In `routes_daily.csv`, `detour_trips` counts the trips that ran during a posted detour that day. Their departures are included in the other columns.
- In `end_of_line_monthly.csv`, `early` counts buses that arrived early, and `p10`, `p50` and `p90` are how late (in seconds; negative is early) the 10%, 50% and 90% marks of those arrivals were. `n_block` and `n_gps` count how many arrivals came from the next trip in the block and how many from GPS (see the [methodology](/methodology/)).

<!-- patterns-files -->

## Per-trip files

`trips/trips_YYYY-MM.csv` has one row per scheduled trip, for one month. A file is written once for each finished month, so the current month isn't there yet. These files are in the `trips` folder of the [stats repository on GitHub](https://github.com/wasthebusontime/stats), not on this site.

- `date` is the service date, `route` and `direction_id` as above, `trip_id` the trip in Intercity Transit's schedule data, and `sched_start` the scheduled time it leaves its first stop (24:00 and later are after midnight).
- `start_delay_s` and `end_delay_s` are how late (in seconds; negative is early) the trip left its first stop and arrived at its last stop, blank when not recorded.
- `block_id` is the chain of trips the bus was scheduled to run, `prev_trip_id` the trip before this one on it, and `sched_layover_s` the scheduled time between the two. `prev_end_delay_s` is how late the previous trip arrived at its last stop.
- `carry_over` is `yes` when the bus, coming off the previous trip, reached the first stop after this trip's scheduled departure, `no` when it didn't (or there was no previous trip on the block), and blank when we can't tell.

They leave out bus numbers and the raw times from Intercity Transit's real-time data.

## License and credit

The statistics are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Suggested credit line:

> Was the Bus On Time? (wasthebusontime.com), CC BY 4.0

## Source data

The statistics are computed from real-time and schedule data provided AS IS by Intercity Transit under the [Sound Transit Open Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use). This site is not affiliated with or endorsed by Intercity Transit.

Stop locations on the map come from Intercity Transit's schedule data. The route lines are simplified from the route shapes in that data, for display only: they are not Intercity Transit's data.

The stop map's background is made from [OpenStreetMap](https://www.openstreetmap.org/copyright) data (© OpenStreetMap contributors, available under the Open Database License), prepared with [Protomaps](https://protomaps.com).

The same files, with their history, are in the [stats repository on GitHub](https://github.com/wasthebusontime/stats).

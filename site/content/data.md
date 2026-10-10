Title: Data

# Data

The statistics on this site can be downloaded as CSV files below. They contain aggregate counts only (for example, how many departures were early, on time or late on a route in a month), not individual bus trips.

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

Columns:

- `scope` is `timepoints` or `all_stops`, the same choice as the toggle on each page. `day_type` is `weekday`, `saturday` or `sunday`. `hour` is the scheduled hour of the day, where 24 and later are trips after midnight that belong to the day before. `direction_id` is 0 or 1.
- `n` is the number of recorded departures. `early`, `on_time` and `late` use 1 min early to 5 min late. `early_alt`, `on_time_alt` and `late_alt` use Intercity Transit's own definition (0 to 5 min late).
- `under`, `m_10` to `m_1`, `m0` to `m19` and `over` count departures by how many minutes late they left: `m0` is 0 to 1 minute late, `m_1` is up to 1 minute early, `under` is more than 10 minutes early and `over` is 20 or more minutes late.
- In `end_of_line_monthly.csv`, `early` counts buses that arrived early, and `p10`, `p50` and `p90` are how late (in seconds; negative is early) the 10%, 50% and 90% marks of those arrivals were.

## License and credit

The statistics are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Suggested credit line:

> Was the Bus On Time? (wasthebusontime.com), CC BY 4.0

## Source data

The statistics are computed from real-time and schedule data provided AS IS by Intercity Transit under the [Sound Transit Open Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use). This site is not affiliated with or endorsed by Intercity Transit.

Stop locations on the map come from Intercity Transit's schedule data. The route lines are simplified from the route shapes in that data, for display only: they are not Intercity Transit's data.

The stop map's background is made from [OpenStreetMap](https://www.openstreetmap.org/copyright) data (© OpenStreetMap contributors, available under the Open Database License), prepared with [Protomaps](https://protomaps.com).

The same files, with their history, are in the [stats repository on GitHub](https://github.com/wasthebusontime/stats).

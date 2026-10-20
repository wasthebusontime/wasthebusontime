# Was the Bus On Time?

Unofficial on-time statistics for Intercity Transit buses, measured from the agency's public real-time data and compared with the schedule in effect each day.

## Collection

**Snapshot**:
One unmodified response from a real-time feed, kept byte for byte.
_Avoid_: poll, sample

**Fetch log**:
The daily record of every fetch attempt: when, which feed, the result, and how fresh the data was.

**Spool**:
Snapshots from the current hour, waiting to be packed.

**Pack**:
One compressed archive holding a feed's snapshots for one completed UTC hour.
_Avoid_: bundle, tarball

**Static version**:
One distinct copy of the agency's static GTFS schedule, as fetched on a given day.
_Avoid_: schedule version, feed (alone)

## Schedule and time

**Service date**:
The schedule day a trip belongs to, which can run past midnight into the next calendar day.
_Avoid_: day, date (alone)

**Service hour**:
The hour of the service date a departure was scheduled in; it can be 24 or more for trips after midnight.

**Day type**:
Weekday, Saturday or Sunday.

**Band**:
A named part of the service day (early, AM peak, midday, PM peak, evening).
_Avoid_: time period, daypart

**Period**:
The span of service dates a set of published numbers covers.

**Timepoint**:
A stop with a time printed in the schedule, which drivers are expected not to leave early.

## Measurement

**Departure**:
One bus leaving one stop on one trip; the thing on-time numbers count.
_Avoid_: stop event (in public text), trip

**Delay**:
How many seconds after its scheduled time a departure left; negative means early.
_Avoid_: lateness, deviation

**On-time window**:
The range of delays that counts as on time. The **headline** window is 1 minute early to 5 minutes late; the **alt** window is Intercity Transit's own, 0 to 5 minutes late.
_Avoid_: on-time definition, threshold

**Early / on time / late**:
Where a departure's delay falls relative to an on-time window.

**Scope**:
Which stops a number covers: **timepoints** or **all stops**, both excluding each trip's last stop.
_Avoid_: stop set, view

**Terminal departure**:
A departure from a trip's first stop.

**End-of-line arrival**:
A trip's arrival at its last stop, reported separately because that stop has no departure.
_Avoid_: last-stop departure

**In time for the next trip**:
An end-of-line arrival followed by another trip on the same block that was not a carry-over: the bus reached the stop before that trip was due to leave. Counted only where the next trip's carry-over is known.
_Avoid_: made its connection, on time (for arrivals)

**Time to spare**:
The next trip's scheduled departure minus the end-of-line arrival, below zero when the bus arrived too late for it.
_Avoid_: recovery time (the schedule's allowance, not what was left of it)

**Observed**:
Recorded with a tracked bus at the time it left, and seen for at least 180 s after it left (so its time had settled); only observed departures count. Public pages say "recorded".
_Avoid_: seen (which only means the stop appeared in the feed)

**Perf block**:
The counts behind one set of on-time numbers: departures, early, on time and late in each window, delay percentiles, and optionally a 1-minute histogram.
_Avoid_: stats, metrics

**Minimum sample**:
The fewest departures a percentage needs before it is shown.

## Patterns

**Block**:
The chain of trips one bus is scheduled to run in a day, from the static GTFS `block_id`.

**Layover**:
The scheduled time between a trip's end and the next trip on its block leaving the same stop.

**Carry-over**:
A trip whose bus, coming off the previous trip on its block, reached the first stop after the trip's scheduled departure, so it could not leave on time. On a layover of 10 minutes or more, judged from the bus's GPS arrival at the end of the previous trip, which must be more than a minute after the departure. The first trip on a block is never a carry-over.
_Avoid_: knock-on delay, late because

**Segment**:
The stretch of one route and direction between two consecutive timepoints (or a timepoint and the trip's last stop), measured by the delay gained over it.
_Avoid_: link, leg

**Delay gained**:
Over one segment of one trip, the arrival delay at its far end minus the departure delay at its near end: the time the bus took between them less the time scheduled. A scheduled wait at a timepoint is in neither segment. Negative when the bus made up time. Only when both ends were recorded; at the last stop the arrival is the end-of-line arrival (from the next trip on the block, or GPS).
_Avoid_: delay added, time lost (in public text, unless qualified)

**Pattern**:
A condition that many late departures share, found by a published rule and shown as an interpretation, not a measurement: general, or for one day type and band. Patterns name conditions, never people or decisions, and never suggest fixes. A carry-over pattern is the share of a route's late departures (by direction and first stop) that were on trips which left their first stop late right after a carry-over.
_Avoid_: cause, reason, "why late", finding (in public text)

## Data quality

**Completeness**:
The share of scheduled trips or departures that were observed.

**Gap**:
A stretch of time, while trips were scheduled to run, when recording failed or the feed was stale.

**Data loss**:
A gap long enough (over about 13 minutes) that departures were lost, published with its cause.
_Avoid_: outage (which is only the cause)

**Not recorded**:
A scheduled trip never seen with a tracked bus; never counted as late or missed.
_Avoid_: missed, cancelled

**Notice**:
A structured warning attached to published numbers: provisional, data loss, low completeness or methodology change.

## Publishing

**Site files**:
The per-page JSON and CSV files the pipeline publishes and the site is built from.
_Avoid_: stats JSON, data files

**Map preset**:
One precomputed stop map view for a period, day type and band.

**Page preset**:
One precomputed set of route and system numbers for a day type and band, from which a filtered home or route page is built. Public and code text call the choice a "filter" or "preset", never a "view" (see **Scope**).
_Avoid_: view, filtered view (in public text and code)

**Synthetic**:
Made-up sample data, marked so it can never be published as real numbers.

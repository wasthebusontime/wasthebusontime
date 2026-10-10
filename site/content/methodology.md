Title: Methodology

# Methodology

How the numbers on this site are measured. The method is provisional while we check it against more data.

## What "on time" means

The headline numbers use a common industry window: a bus is on time if it *departs* a stop between 1 minute before and 5 minutes after the scheduled time. Leaving more than 1 minute before the scheduled time counts as early; leaving more than 5 minutes after it counts as late. We use this window so the numbers are easy to compare with other transit agencies' and with other places this method is used.

| Label on this site | Early | On time | Late |
|---|---|---|---|
| On time (1 min early to 5 min late) | more than 1 min before | 1 min before to 5 min after | more than 5 min after |

Intercity Transit's 2026 Transit Development Plan sets a standard that 90% of buses leave terminal departure points (the first stop of each trip) on time. The agency counts a bus as on time only from 0 to 5 minutes after the scheduled time, a little stricter than the window above on the early side.

<!-- Hidden for now: a second row for Intercity Transit's own definition.
| On time (Intercity Transit's own definition: 0 to 5 min late) | before the scheduled time | 0 to 5 min after | more than 5 min after |
-->

## Departures, not arrivals

On-time status is measured when the bus leaves a stop, as the agency does. The last stop of a trip has no departure, so it is not part of the on-time percentages. Arrivals at the last stop are shown separately as end-of-line arrivals; buses often arrive there early because schedules include recovery time at the end of a trip.

We count departures, not trips. A departure is one bus leaving one stop on one trip, so a single trip adds several departures: one for each timepoint it leaves in the timepoints view, or one for each stop in the all stops view. Trips are counted separately, as scheduled, recorded and not recorded, on each route page and on the [Data quality](/data-quality/) page.

## Which stops

Every page has a choice between two sets of stops:

- **Timepoints** (the default): the stops with times printed in the schedule, not counting each trip's last stop. Drivers are expected not to leave them early. This matches how Intercity Transit measures on-time performance.
- **All stops**: every stop, not counting each trip's last stop. Times at stops between timepoints are estimates the schedule fills in, not promises, so a bus can be "early" there without having left a timepoint early. Expect lower on-time numbers in this view.

**Terminal departures** (the first stop of each trip) are also shown on their own, for comparison with the agency's 90% standard. The comparison is approximate, because the agency's window differs slightly (see above).

## Where the times come from

Intercity Transit publishes real-time data about its buses. For a while after a bus leaves a stop, that data keeps reporting when it left. We record that data around the clock and use those reported departure times. A departure counts only if the data shows the bus was tracked when it left; times that are only predictions are never used.

Each departure is compared with the schedule that was in effect on that day, including holiday schedules, trips after midnight and daylight saving time changes.

## Completeness and gaps

- Departures that weren't recorded are left out. They are never guessed and never counted as late or missed.
- A trip with no tracked bus may have been cancelled, or may have run without working tracking. We can't tell the difference, so it is reported as **not recorded**.
- Short interruptions in our recording (up to about 14 minutes) lose almost nothing, because the data keeps recent departures for a while. The exception is the last few stops of a trip that ends during the interruption. Longer gaps lose data; they are listed on the [Data quality](/data-quality/) page and noted on affected pages.
- A percentage is shown only when at least 30 departures are behind it. Otherwise the page says "Not enough data".

## Rounding

Percentages are computed from counts and rounded to whole percent in headlines and to one decimal place in tables, so the parts of a headline may not add up to exactly 100%.

## Limitations

- **These are not the agency's numbers.** We use the same tracking system's public output, but the agency may use internal data, rounding or exclusions we can't see. Expect differences.
- **Measurement point.** Departure times come from the tracking system's detection of a bus leaving a stop area, not from door sensors. A bus that leaves exactly on schedule may be recorded a few seconds either side.
- **Terminal departures** may read slightly early, because buses often wait away from the exact stop location. This is being checked.
- **Gaps.** Recording runs on home hardware; outages longer than about 14 minutes lose data, and the site says so.
- **Unofficial.** This site is not affiliated with or endorsed by Intercity Transit.

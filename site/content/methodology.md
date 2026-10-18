Title: Methodology

# Methodology

How the numbers on this site are measured. The method is provisional while we check it against more data.

## What "on time" means

The headline numbers use a common industry window: a bus is on time if it *departs* a stop between 1 minute before and 5 minutes after the scheduled time. Leaving more than 1 minute before the scheduled time counts as early; leaving more than 5 minutes after it counts as late. We use this window so the numbers are easy to compare with other transit agencies' and with other places this method is used.

| Label on this site | Early | On time | Late |
|---|---|---|---|
| On time (1 min early to 5 min late) | more than 1 min before | 1 min before to 5 min after | more than 5 min after |

## How this differs from Intercity Transit's measure

Intercity Transit's 2026 Transit Development Plan counts a bus as on time only if it leaves a timepoint from 0 to 5 minutes after the scheduled time, and sets a goal that 90% of buses leave terminal departure points (the first stop of each trip) on time.

We use the 1 minute early to 5 minutes late window everywhere on this site, the first stop of each trip included, because:

- We measure every timepoint, not only the first stop of each trip.
- The 1 minute early to 5 minutes late window is close to how many other transit agencies measure on-time performance.
- One window keeps the numbers on every page comparable with each other.
- Anyone can use our code to measure another transit agency, so the main numbers shouldn't depend on one agency's own rule.

We still compute Intercity Transit's version. It is in the CSV files on the [Data](/data/) page, with a column for each window.

<!-- agency-comparison -->

Intercity Transit also publishes on-time figures by route for 2025. Those describe the routes before the agency's May 2026 redesign, so they can't be compared with the numbers here.

## Departures, not arrivals

On-time status is measured when the bus leaves a stop, as the agency does. The last stop of a trip has no departure, so it is not part of the on-time percentages. Arrivals at the last stop are shown separately as end-of-line arrivals; buses often arrive there early because schedules include recovery time at the end of a trip.

We count departures, not trips. A departure is one bus leaving one stop on one trip, so a single trip adds several departures: one for each timepoint it leaves in the timepoints view, or one for each stop in the all stops view. Trips are counted separately, as scheduled, recorded and not recorded, on each route page and on the [Data quality](/data-quality/) page.

## Which stops

Every page has a choice between two sets of stops:

- **Timepoints** (the default): the stops with times printed in the schedule, not counting each trip's last stop. Drivers are expected not to leave them early. This matches how Intercity Transit measures on-time performance.
- **All stops**: every stop, not counting each trip's last stop. Times at stops between timepoints are estimates the schedule fills in, not promises, so a bus can be "early" there without having left a timepoint early. Expect lower on-time numbers in this view.

**Terminal departures** (the first stop of each trip) are also shown on their own, next to Intercity Transit's 90% goal for them. They use the same window as everything else, so they compare with the goal only roughly (see above).

## Where the times come from

Intercity Transit publishes real-time data about its buses. For a while after a bus leaves a stop, that data keeps reporting when it left. We record that data around the clock and use those reported departure times. A departure counts only if the data shows the bus was tracked when it left, and only if we saw it for at least 3 minutes after the bus left, because a departure time can still be corrected in the first few minutes. Times that are only predictions are never used.

Each departure is compared with the schedule that was in effect on that day, including holiday schedules, trips after midnight and daylight saving time changes.

## Completeness and gaps

- Departures that weren't recorded are left out. They are never guessed and never counted as late or missed.
- A trip with no tracked bus may have been cancelled, or may have run without working tracking. We can't tell the difference, so it is reported as **not recorded**.
- Short interruptions in our recording (up to about 13 minutes) lose almost nothing, because the data keeps recent departures for a while. The exception is the last few stops of a trip that ends during the interruption. Longer gaps lose data; they are listed on the [Data quality](/data-quality/) page and noted on affected pages.
- A percentage is shown only when at least 30 departures are behind it. Otherwise the page says "Not enough data".

## Rounding

Percentages are computed from counts and rounded to whole percent in headlines and to one decimal place in tables, so the parts of a headline may not add up to exactly 100%.

## Limitations

- **These are not the agency's numbers.** We use a different on-time window (see above) and the same tracking system's public output, but the agency may use internal data, rounding or exclusions we can't see. Expect differences.
- **Measurement point.** Departure times come from the tracking system's detection of a bus leaving a stop area, not from door sensors. A bus that leaves exactly on schedule may be recorded a few seconds either side.
- **Terminal departures.** At the first stop of a trip, the recorded departure tends to come about 20 seconds before the bus is clear of the stop. About 5% of terminal departures are recorded 0 to 30 seconds early, so under Intercity Transit's stricter definition some of them may really have been on time.
- **Large stations.** At a few large stations, departures tend to be recorded early: about 45 seconds early at Hawks Prairie Park & Ride, and about 20 seconds early at transit center bays.
- **Gaps.** Recording runs on home hardware; outages longer than about 13 minutes lose data, and the site says so.
- **Unofficial.** This site is not affiliated with or endorsed by Intercity Transit.

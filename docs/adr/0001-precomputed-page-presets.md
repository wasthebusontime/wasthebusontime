# Precomputed page presets, one static page per preset

Status: accepted (2026-10-18)

The home page and route pages can be filtered by day type (all days, weekdays, Saturday, Sunday) and time of day (all day or one band). The pipeline computes every combination ahead of time as **page presets** (`site/presets/{daytype}-{band}/system.json` and `routes/{slug}.json`, following the stop map's map presets), and the site builds each preset as its own static page. Charts stay in Python and every filtered page works without JavaScript, at the cost of many more files.

## Considered options

- **All presets inside one page**, shown and hidden with CSS like the scope switch. Rejected: 24 versions would make Route 13 about 3 MB.
- **Drawing in the browser from JSON.** Rejected: the charts would move from Python to JavaScript, and readers without JavaScript would only get the unfiltered page.
- **Rendering per request** (Cloudflare Workers). Rejected: a running service to keep up for something a nightly build does cheaply.

## Consequences

- **Zero service, no file.** A route, or the whole system, with no scheduled departures in a preset has no preset file, and the site neither builds nor links that filtered page (the filter choice is shown grayed out). Saturday and Sunday early, for example, have no service, so those presets have no folder at all. The alternative, a file with zero scheduled departures, would add files the site could only skip.
- **File count and larger agencies.** Cloudflare Pages on the Free plan allows at most 20,000 files per site. Each page has 23 presets besides the unfiltered one: on Intercity Transit's 30 routes a real run writes 593 preset files (21 system, 572 route) and as many filtered pages. A larger agency multiplies this by its route count; the cap leaves room for several hundred routes. If the project is cloned for a larger agency, revisit this (browser rendering, or a different host).
- **Stop pages are excluded.** Filtering 1,118 stop pages the same way would need about 27,000 files, over the cap. Stop pages and the stops map keep their own filters.
- **Whole period only.** Presets cover the published period; monthly presets, like the map's, are out of scope.
- **Filtered pages are not indexed.** They carry `noindex, follow` and stay out of the sitemap, so search engines see only the unfiltered pages.
- **Departures and arrivals count where they happened in the day.** Each departure is in the band of its own scheduled time, not its trip's start, and an end-of-line arrival is in the band of its scheduled arrival. A trip leaving at 5:50 pm and ending at 6:20 pm counts its departures under PM peak and its arrival under evening.

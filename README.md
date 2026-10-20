# Was the Bus On Time?

An **unofficial**, open-source project that collects Intercity Transit's public GTFS / GTFS-Realtime data and publishes on-time performance statistics.

> **This project is not affiliated with, endorsed by, or associated with Intercity Transit in any way.**

## Status
Early development. The [collector](collector/) archives the real-time data, the [statistics pipeline](pipeline/) turns it into on-time statistics, and the [site](site/) generator builds the website. The website isn't live yet, and no statistics are published yet.

## Measuring a different agency
The method isn't specific to Intercity Transit, and we try to keep it that way. To point a copy of this code at another agency's GTFS and GTFS-Realtime feeds, change:

- **Feeds:** `REALTIME_BASE` and `STATIC_URL` in [collector/src/wbot_collector/config.py](collector/src/wbot_collector/config.py).
- **The agency:** its name, its on-time goal for terminal departures and its time zone in [site/src/wbot_site/agency.py](site/src/wbot_site/agency.py). The pipeline uses the same time zone.
- **On-time windows:** `HEADLINE` (the main numbers) and `ALT` (the agency's own definition, if it has one) in [pipeline/src/wbot_pipeline/config.py](pipeline/src/wbot_pipeline/config.py). The site's wording, charts and map follow from them.
- **Start date:** `WBOT_PIPELINE_FIRST_SERVICE_DATE` (see [pipeline/deploy/pipeline.env.example](pipeline/deploy/pipeline.env.example)).
- **Words written for this agency:** the pages in [site/content/](site/content/), the site name, description and service area in `site/src/wbot_site/templates/base.html`, and the data terms in `footer.html`.

The method also relies on how this agency's real-time data behaves: how long departed stops stay in the feed, and whether trips carry block ids and vehicle ids. Check those for a new agency before trusting the numbers ([methodology](site/content/methodology.md)).

**Site size.** The site is static: every page is built ahead of time, and nothing runs on a server. Each choice in the day type and time of day filters on the home and route pages is its own page, so a route can have up to 24 pages, plus one page per stop. (The stops map filters in the browser instead, because the map needs JavaScript anyway; the other pages work without it.) This copy has 30 routes and about 1,100 stops and builds about 1,850 files. Cloudflare Pages' free plan allows 20,000 files, and the build stops at 15,000 (`MAX_FILES` in [site/src/wbot_site/build.py](site/src/wbot_site/build.py)). For an agency with many more routes or stops, leave the filters out (stats without a `presets/` folder build a site without them) or host the site somewhere without a file limit. Why the filters work this way: [docs/adr/0001-precomputed-page-presets.md](docs/adr/0001-precomputed-page-presets.md).

## Licenses
- **Code:** [MIT](LICENSE)
- **Site content and computed statistics:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE-CONTENT)
- **Transit data:** not covered by either license above. See [Data source and attribution](#data-source-and-attribution).

## Data source and attribution
Schedule and real-time data are provided **"AS IS"** by Intercity Transit. The data is used under the
[Sound Transit Open Transit Data: Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use).
Intercity Transit retains all rights to its data and trademarks. Anyone who obtains transit data through this project is also bound by those terms.

## Contact
contact@wasthebusontime.com

# Was the Bus On Time?

An **unofficial**, open-source project that collects Intercity Transit's public GTFS / GTFS-Realtime data and publishes on-time performance statistics.

> **This project is not affiliated with, endorsed by, or associated with Intercity Transit in any way.**

## Status
Early development. The [collector](collector/) archives the real-time data, the [statistics pipeline](pipeline/) turns it into on-time statistics, and the [site](site/) generator builds the website. The website isn't live yet, and no statistics are published yet.

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

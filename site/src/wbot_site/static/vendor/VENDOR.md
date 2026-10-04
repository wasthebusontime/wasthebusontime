# Vendored files

Third-party files served from our own domain, so visitors never load anything from another host. Only the stop map (`/stops/`) uses them. Pinned versions; update deliberately and note the date here.

| Path | What | Version | Source | License |
|---|---|---|---|---|
| `maplibre-gl/` | MapLibre GL JS, the map renderer (ES modules plus CSS) | 6.12.0 | npm `maplibre-gl`, `dist/` | BSD-3-Clause (`maplibre-gl/LICENSE.txt`) |
| `pmtiles/pmtiles.js` | Reads the `.pmtiles` tile file with HTTP range requests | 4.5.0 | npm `pmtiles`, `dist/pmtiles.js` | BSD-3-Clause (`pmtiles/LICENSE`) |
| `protomaps/basemaps.js` | Basemap style layers (we use the "grayscale" flavor) | 5.7.2 | npm `@protomaps/basemaps`, `dist/basemaps.js` | BSD-3-Clause (`protomaps/LICENSE`) |
| `protomaps/fonts/` | Noto Sans Regular, Medium and Italic glyphs, Latin ranges only (0-255, 256-511, 8192-8447) | basemaps-assets `main`, 2026-10-04 | github.com/protomaps/basemaps-assets `fonts/` | SIL Open Font License (`protomaps/fonts/OFL.txt`) |
| `protomaps/sprites/` | Grayscale map icons, v4 | basemaps-assets `main`, 2026-10-04 | github.com/protomaps/basemaps-assets `sprites/v4/` | MIT, derived from tangrams/icons (`protomaps/sprites/LICENSE.md`) |

The map tiles themselves are not in this repository: see `site/tools/make_tiles.sh`. They are OpenStreetMap data (© OpenStreetMap contributors, ODbL) processed by Protomaps, credited on the map and on `/data/`.

Fetched 2026-10-04 from registry.npmjs.org tarballs and raw.githubusercontent.com.

**Upgrading MapLibre:** `map.js` imports `maplibre-gl.mjs` by a fixed path, and it loads its shared and worker modules by relative path, so a browser could mix cached old and new files. Put the new version in a new folder (for example `maplibre-gl-7/`) and update the import in `map.js` and the stylesheet link in `stops.html`. The other files are linked with a content fingerprint by the build, so they are fetched fresh when they change.

#!/usr/bin/env bash
# Cuts the basemap tile file for the stop map out of a Protomaps daily planet build.
#
#   bash tools/make_tiles.sh [out.pmtiles] [YYYYMMDD]
#
# Needs the pmtiles CLI (github.com/protomaps/go-pmtiles) on PATH. It reads only the
# byte ranges it needs from the remote build, so it downloads about as much as the
# output size. The result is OpenStreetMap data (ODbL): credit "© OpenStreetMap
# contributors" wherever it is shown. It is not committed: dev serves it from VM 301
# (/srv/wbot-dev/tiles), prod from Cloudflare R2. Refresh a few times a year.
set -euo pipefail

# About Thurston County, which covers Intercity Transit's service area.
BBOX=-123.21,46.73,-122.39,47.20
MAXZOOM=15

out=${1:-olympia.pmtiles}
build=${2:-$(curl -sf https://build-metadata.protomaps.dev/builds.json | python3 -c 'import json,sys; print(json.load(sys.stdin)[-1]["key"][:8])')}

pmtiles extract "https://build.protomaps.com/$build.pmtiles" "$out" --bbox="$BBOX" --maxzoom="$MAXZOOM"
echo "$out from Protomaps build $build, bbox $BBOX, maxzoom $MAXZOOM"

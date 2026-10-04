#!/usr/bin/env bash
# Builds the static site. The single entry point for the dev VM and Cloudflare Pages.
#
#   WBOT_ENV        dev (default: preview banner, noindex) or prod
#   WBOT_STATS_DIR  stats directory to build from; if unset, the public stats repo is
#                   cloned (--depth 1). In dev, a stats dir without site output falls
#                   back to the committed sample with a "SAMPLE DATA" banner.
#   WBOT_OUT_DIR    output directory (default: site/dist)
set -euo pipefail

UV_VERSION=0.12.23
STATS_REPO=https://github.com/wasthebusontime/stats.git

site_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$site_dir"
export WBOT_ENV=${WBOT_ENV:-dev}

if ! command -v uv >/dev/null 2>&1; then
    # Cloudflare Pages has Python and pip but no uv.
    python3 -m pip install --quiet --user "uv==$UV_VERSION"
    export PATH="$HOME/.local/bin:$PATH"
fi

if [ -n "${WBOT_STATS_DIR:-}" ]; then
    stats_dir=$WBOT_STATS_DIR
else
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    stats_dir=$tmp/stats
    git clone --quiet --depth 1 "$STATS_REPO" "$stats_dir"
fi

commit_of() {
    if [ -e "$1/.git" ]; then git -C "$1" rev-parse --short HEAD; else echo none; fi
}
export WBOT_CODE_COMMIT=${WBOT_CODE_COMMIT:-$(git -C "$site_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)}
export WBOT_STATS_COMMIT=${WBOT_STATS_COMMIT:-$(commit_of "$stats_dir")}

uv run --frozen --no-dev python -m wbot_site.build --stats "$stats_dir" --out "${WBOT_OUT_DIR:-dist}"

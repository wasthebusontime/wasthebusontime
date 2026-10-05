#!/usr/bin/env bash
# Evaluation-phase copy of the pipeline's site files to the dev VM (VM 301), so the dev
# site shows real numbers before anything is published. Runs as wbot on VM 300 from
# wbot-devcopy.service, which starts after each successful nightly pipeline run.
#
# Temporary: remove it when publishing is turned on (steps in README.md next to this
# file). Until then it also switches itself off: with WBOT_PIPELINE_PUBLISH=1 it copies
# nothing, and the dev build prefers the public stats repo once that has data.
set -euo pipefail

out=${WBOT_DERIVED_DIR:-/srv/wbot/data/derived}/out
key=${WBOT_DEVCOPY_KEY:-/var/lib/wbot/.ssh/devcopy}
dest=${WBOT_DEVCOPY_DEST:-devcopy@192.168.1.181}

if [ "${WBOT_PIPELINE_PUBLISH:-0}" = 1 ]; then
    echo "publishing is on, so the dev site builds from the stats repo; nothing copied"
    exit 0
fi
if [ ! -f "$out/site/meta.json" ]; then
    echo "no site files in $out yet; nothing copied"
    exit 0
fi

# The receiving key is locked to rrsync in one directory, so paths are relative to it.
rsync -a --delete --delay-updates \
    -e "ssh -i $key -o BatchMode=yes -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/lib/wbot/.ssh/known_hosts" \
    "$out/site" "$out/csv" "$dest:"
echo "copied site/ and csv/ to $dest"

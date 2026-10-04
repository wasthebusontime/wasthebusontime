#!/usr/bin/env bash
# Dev VM (VM 301) rebuild job. Runs as wbotdev from systemd:
#
#   rebuild.sh          (wbot-dev-poll, every 5 minutes) build only if the main repo's
#                       dev branch or the stats repo's main branch has a commit that
#                       hasn't been tried yet
#   rebuild.sh --force  (wbot-dev-rebuild) build now
#
# A build resets both clones to origin, runs site/build.sh with WBOT_ENV=dev into
# releases/<UTC timestamp>/ and, on success, switches releases/current to it
# atomically. On failure the previous site stays up and the log is shown at /_status/.
# Every attempt is recorded, so a failing commit pair is not retried every 5 minutes.
#
# Everything is inside main() because the script resets the clone it lives in: bash
# reads the whole function before running it, so a new version takes effect next run.
set -euo pipefail

main() {
    local root=${WBOT_DEV_ROOT:-/srv/wbot-dev}
    local code_url=${WBOT_CODE_URL:-https://github.com/wasthebusontime/wasthebusontime.git}
    local code_branch=${WBOT_CODE_BRANCH:-dev}
    local stats_url=${WBOT_STATS_URL:-https://github.com/wasthebusontime/stats.git}
    local stats_branch=${WBOT_STATS_BRANCH:-main}
    local keep=3
    local state=$root/state releases=$root/releases
    mkdir -p "$state/status" "$releases"

    exec 9>"$state/lock"
    if ! flock -n 9; then
        echo "another build is running"
        return 0
    fi

    local code_head stats_head wanted
    code_head=$(remote_head "$code_url" "$code_branch")
    stats_head=$(remote_head "$stats_url" "$stats_branch")
    wanted="code $code_head stats $stats_head"
    if [ "${1:-}" != "--force" ] && [ "$wanted" = "$(cat "$state/last_attempt" 2>/dev/null || true)" ]; then
        return 0
    fi

    local ts release log result
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    release=$releases/$ts
    log=$state/build.log
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') building $wanted" | tee "$log"

    if sync_clone "$root/code" "$code_url" "$code_branch" >>"$log" 2>&1 &&
        sync_clone "$root/stats" "$stats_url" "$stats_branch" >>"$log" 2>&1 &&
        WBOT_ENV=dev WBOT_STATS_DIR="$root/stats" WBOT_OUT_DIR="$release" \
            bash "$root/code/site/build.sh" >>"$log" 2>&1; then
        # Relative link, renamed over the old one: the switch is atomic.
        ln -sfn "$ts" "$releases/current.tmp"
        mv -T "$releases/current.tmp" "$releases/current"
        echo "$wanted" >"$state/last_success"
        prune "$releases" "$keep"
        result=ok
    else
        rm -rf "$release"
        result=failed
    fi
    echo "$wanted" >"$state/last_attempt"
    write_status "$state" "$result" "$wanted"
    echo "build $result" | tee -a "$log"
    [ "$result" = ok ]
}

remote_head() {
    local head
    head=$(git ls-remote "$1" "refs/heads/$2" | cut -f1)
    if [ -z "$head" ]; then
        echo "can't read $2 from $1" >&2
        return 1
    fi
    echo "${head:0:12}"
}

sync_clone() {
    local dir=$1 url=$2 branch=$3
    if [ ! -d "$dir/.git" ]; then
        git clone --quiet "$url" "$dir"
    fi
    git -C "$dir" fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    git -C "$dir" checkout --quiet --force -B "$branch" "origin/$branch"
    git -C "$dir" reset --quiet --hard "origin/$branch"
    git -C "$dir" clean --quiet -fdx -e .venv
}

prune() {
    local releases=$1 keep=$2
    find "$releases" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' | sort -r | tail -n "+$((keep + 1))" |
        while read -r old; do rm -rf "${releases:?}/$old"; done
}

write_status() {
    local state=$1 result=$2 wanted=$3 tmp
    tmp=$(mktemp "$state/status/.index.XXXXXX")
    {
        echo '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        echo '<title>Dev build status</title><body style="font-family:monospace;max-width:60rem;margin:1rem auto">'
        echo "<h1>Last build: $result</h1>"
        echo "<p>$(date -u '+%Y-%m-%d %H:%M:%S UTC'), $wanted</p>"
        echo "<p>Live: $(readlink "$(dirname "$state")/releases/current" || echo none), $(cat "$state/last_success" 2>/dev/null || echo 'no successful build yet')</p>"
        echo '<pre>'
        sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' "$state/build.log"
        echo '</pre>'
    } >"$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$state/status/index.html"
}

main "$@"
exit

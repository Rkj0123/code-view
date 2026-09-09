#!/bin/sh
set -eu

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'USAGE'
Usage: code-view [host options]

Analyze the current directory and open the Code View browser.
Extra options are passed to the local host, for example:
  code-view --editor code
  code-view --no-watch
  code-view --allow-command

The default mode is static and read-only. Press Ctrl-C in this terminal to stop.
USAGE
    exit 0
fi

CODE_VIEW_HOME="${CODE_VIEW_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
repository=${PWD}

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/code-view.XXXXXX")"
fifo="$tmp_dir/startup"
host_pid=""
cleanup() {
    rm -f "$fifo"
    rmdir "$tmp_dir" 2>/dev/null || true
}
stop_on_signal() {
    if [ -n "$host_pid" ]; then kill "$host_pid" 2>/dev/null || true; fi
    exit 130
}
trap cleanup EXIT
trap stop_on_signal INT TERM HUP
mkfifo "$fifo"
"$CODE_VIEW_HOME/script/code-view.sh" "$repository" "$@" >"$fifo" &
host_pid=$!

while IFS= read -r line; do
    printf '%s\n' "$line"
    case "$line" in
        *'"url"'*)
            url=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["url"])' "$line" 2>/dev/null || true)
            if [ -n "$url" ]; then
                if command -v open >/dev/null 2>&1; then
                    open "$url" >/dev/null 2>&1 || printf 'Open this URL manually: %s\n' "$url"
                else
                    printf 'Open this URL manually: %s\n' "$url"
                fi
            fi
            ;;
    esac
done <"$fifo"

host_status=0
wait "$host_pid" || host_status=$?
exit "$host_status"

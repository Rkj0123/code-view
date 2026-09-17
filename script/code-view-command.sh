#!/bin/sh
set -eu

CODE_VIEW_HOME="${CODE_VIEW_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"

if [ "${1:-}" = "--version" ] || [ "${1:-}" = "-v" ]; then
    if [ -f "$CODE_VIEW_HOME/VERSION" ]; then
        printf 'code-view %s\n' "$(cat "$CODE_VIEW_HOME/VERSION")"
    else
        printf 'code-view %s\n' "0.1.0"
    fi
    exit 0
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'USAGE'
Usage: code-view [options]

Analyze the current directory and open the Code View browser.

Options:
  --version, -v        Display current version
  --update             Check for updates immediately and update if available
  --no-update          Skip checking for updates for this run
  --help, -h           Show this help message

Extra options are passed to the local host, for example:
  code-view --editor code
  code-view --no-watch
  code-view --allow-command

The default mode is static and read-only. Press Ctrl-C in this terminal to stop.
USAGE
    exit 0
fi

# Check for explicit update command or update bypass flags
skip_update=0
force_update=0
for arg do
    case "$arg" in
        --no-update) skip_update=1 ;;
        --update) force_update=1 ;;
        *) ;;
    esac
done

if [ "$force_update" = "1" ] && [ "$#" -eq 1 ]; then
    exec "${CODE_VIEW_PYTHON:-python3}" "$CODE_VIEW_HOME/script/updater.py" --update
fi

# Rebuild argument list without internal updater flags
for arg do
    case "$arg" in
        --no-update|--update) ;;
        *) set -- "$@" "$arg" ;;
    esac
    shift
done

# Run auto-update check unless explicitly skipped
if [ "${CODE_VIEW_NO_UPDATE:-0}" != "1" ] && [ "$skip_update" != "1" ]; then
    updater_flags="--check-and-update"
    if [ "$force_update" = "1" ]; then
        updater_flags="--update"
    fi
    update_rc=0
    "${CODE_VIEW_PYTHON:-python3}" "$CODE_VIEW_HOME/script/updater.py" $updater_flags || update_rc=$?
    if [ "$update_rc" -eq 10 ]; then
        # Updated successfully! Re-execute with updated codebase.
        CODE_VIEW_NO_UPDATE=1 exec "$0" "$@"
    fi
fi

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

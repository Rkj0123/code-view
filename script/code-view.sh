#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_only=0
if [ "${1:-}" = "--build-only" ]; then
    build_only=1
    shift
fi
repository=${1:-$PWD}
if [ "$#" -gt 0 ]; then
    shift
fi

web="$root/services/web-canvas"
host="$root/services/local-server"
build="$host/build"

if [ ! -d "$web/node_modules" ]; then
    npm ci --prefix "$web"
fi
npm run build --prefix "$web"
cmake -S "$host" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build "$build"

if [ "$build_only" = 1 ]; then exit 0; fi

# Installed launchers pin the interpreter that passed dependency validation.
# Preserve an explicitly supplied analyzer command for development adapters.
custom_analyzer=0
for argument do
    case "$argument" in --analyzer-arg|--analyzer-arg=*) custom_analyzer=1 ;; esac
done
if [ -n "${CODE_VIEW_PYTHON:-}" ] && [ "$custom_analyzer" = 0 ]; then
    set -- --analyzer-arg "$CODE_VIEW_PYTHON" --analyzer-arg "$root/services/python-analyzer/analyzer.py" "$@"
fi

exec "$build/code-view-local-server" \
    --repo "$repository" \
    --web-root "$web/dist" \
    "$@"

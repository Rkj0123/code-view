#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
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

exec "$build/code-view-local-server" \
    --repo "$repository" \
    --web-root "$web/dist" \
    "$@"

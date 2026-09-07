#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
host_test="$root/services/local-server/build/code-view-local-server-tests"
if [ -x "$host_test" ]; then
    cmake --build "$root/services/local-server/build" -j4
else
    printf '%s\n' 'Host gate is not built. Run: cmake -S services/local-server -B services/local-server/build -G Ninja -DBUILD_TESTING=ON && cmake --build services/local-server/build'
    exit 1
fi

python3 -m unittest discover -s "$root/services/python-analyzer" -p 'test_*.py' &
analyzer_pid=$!
python3 -m unittest discover -s "$root/tests/docs" -p 'test_*.py' &
docs_pid=$!
npm test --prefix "$root/services/web-canvas" -- --reporter=dot &
web_pid=$!
"$host_test" --gate &
host_pid=$!
failed=0
for pid in "$analyzer_pid" "$docs_pid" "$web_pid" "$host_pid"; do
    if ! wait "$pid"; then failed=1; fi
done
exit "$failed"

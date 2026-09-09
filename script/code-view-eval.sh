#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$root/evals/python-analyzer/evaluate.py"
python3 "$root/evals/performance/evaluate.py"
python3 "$root/evals/docs/evaluate.py"
python3 "$root/evals/installer/evaluate.py"
npm run eval --prefix "$root/services/web-canvas"

host_eval="$root/services/local-server/build/code-view-local-server-eval"
if [ -x "$host_eval" ]; then
    ctest --test-dir "$root/services/local-server/build" --output-on-failure
    "$host_eval"
    python3 "$root/services/local-server/evals/reliability.py"
else
    printf '%s\n' 'Host eval is not built. Run: cmake -S services/local-server -B services/local-server/build -G Ninja -DBUILD_TESTING=ON && cmake --build services/local-server/build'
    exit 1
fi

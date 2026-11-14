#!/usr/bin/env bash
# Runs JellyAshy from the source tree (JELLYASHY_DEVTOOLS=1 opens the inspector on right click).
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m jellyashy_desktop "$@"

#!/usr/bin/env bash
# Builds JellyAshy's web app for Linux into src/jellyashy_desktop/web and
# takes its version. The web app lives in the JellyAshy repository:
#
#   tools/update-web.sh [path/to/JellyAshy]   (default: ../JellyAshy)
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
src=$(cd "${1:-$here/../JellyAshy}" && pwd)
out="$here/src/jellyashy_desktop/web"

(cd "$src/web" && LINUX_OUT="$out" node build.mjs linux >/dev/null)
version=$(node -p "require('$src/web/package.json').version")
sed -i "s/^APP_VERSION = \".*\"/APP_VERSION = \"$version\"/" "$here/src/jellyashy_desktop/__init__.py"
echo "web app $version -> $out"

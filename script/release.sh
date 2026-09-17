#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ $# -lt 1 ]; then
    printf 'Usage: %s <new-version> (e.g. 0.1.1 or v0.1.1)\n' "$0" >&2
    exit 1
fi

NEW_VERSION="${1#v}"

# Validate SemVer pattern (e.g. 0.1.0, 1.2.3, 1.0.0-rc1)
case "$NEW_VERSION" in
    [0-9]*.[0-9]*.[0-9]*| [0-9]*.[0-9]*.[0-9]*-*) ;;
    *)
        printf 'Error: Version "%s" is not a valid semantic version (e.g. 0.1.1 or 1.0.0-rc1)\n' "$NEW_VERSION" >&2
        exit 1
        ;;
esac

TAG="v$NEW_VERSION"

# Check if tag already exists
if git rev-parse "$TAG" >/dev/null 2>&1; then
    printf 'Error: Tag "%s" already exists.\n' "$TAG" >&2
    exit 1
fi

# Ensure working directory is clean
if [ -n "$(git status --porcelain)" ]; then
    printf 'Error: Git working tree has uncommitted changes. Please commit or stash them first.\n' >&2
    exit 1
fi

printf 'Updating version to %s...\n' "$NEW_VERSION"

# 1. Update VERSION file
printf '%s\n' "$NEW_VERSION" > "$ROOT_DIR/VERSION"

# 2. Update services/web-canvas/package.json
WEB_PKG="$ROOT_DIR/services/web-canvas/package.json"
if [ -f "$WEB_PKG" ]; then
    python3 -c "
import json
with open('$WEB_PKG', 'r') as f:
    data = json.load(f)
data['version'] = '$NEW_VERSION'
with open('$WEB_PKG', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
fi

# Commit and tag
git add "$ROOT_DIR/VERSION" "$WEB_PKG"
git commit -m "chore: release $TAG"
git tag -a "$TAG" -m "Release $TAG"

printf '\n✓ Version %s released and tagged locally as %s.\n' "$NEW_VERSION" "$TAG"
printf 'To publish to GitHub, run:\n'
printf '  git push upstream main %s\n' "$TAG"
printf '  (or git push origin main %s)\n\n' "$TAG"

#!/bin/sh
set -eu

CODE_VIEW_REPO_URL="${CODE_VIEW_REPO_URL:-https://github.com/Rkj0123/code-view.git}"
CODE_VIEW_REF="${CODE_VIEW_REF:-codex/code-view-revamp}"
HOME_DIR="${HOME:?HOME is required}"
INSTALL_ROOT="${CODE_VIEW_INSTALL_ROOT:-$HOME_DIR/.local/share/code-view}"
BIN_DIR="${CODE_VIEW_BIN_DIR:-}"
SKIP_DEPENDENCIES="${CODE_VIEW_SKIP_DEPENDENCIES:-0}"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'USAGE'
Code View installer

Installs the local Code View runtime and a `code-view` command. The command
analyzes the directory in which it is run and opens the browser locally.

Environment overrides for maintainers:
  CODE_VIEW_REPO_URL       source Git URL
  CODE_VIEW_REF            source Git ref
  CODE_VIEW_INSTALL_ROOT   per-user checkout directory
  CODE_VIEW_BIN_DIR        command installation directory
USAGE
    exit 0
fi

if [ "$(uname -s)" != "Darwin" ]; then
    printf '%s\n' 'Code View currently supports macOS only.' >&2
    exit 2
fi

brew_path() {
    if command -v brew >/dev/null 2>&1; then
        command -v brew
    elif [ -x /opt/homebrew/bin/brew ]; then
        printf '%s\n' /opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        printf '%s\n' /usr/local/bin/brew
    fi
}

BREW="$(brew_path || true)"
if [ -z "$BREW" ] && [ "$SKIP_DEPENDENCIES" != "1" ]; then
    printf '%s\n' 'Homebrew is missing. Installing it with the official installer.'
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    BREW="$(brew_path || true)"
fi

if [ "$SKIP_DEPENDENCIES" != "1" ]; then
    if [ -z "$BREW" ]; then
        printf '%s\n' 'Homebrew was not found after installation.' >&2
        exit 2
    fi
    for tool in cmake ninja node npm python3 git; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            case "$tool" in
                python3) formula=python ;;
                *) formula=$tool ;;
            esac
            "$BREW" install "$formula"
        fi
    done
    if ! "$BREW" list --formula qthttpserver >/dev/null 2>&1; then
        "$BREW" install qthttpserver
    fi
fi

if [ -n "${CODE_VIEW_SOURCE_ROOT:-}" ]; then
    INSTALL_ROOT="$(CDPATH= cd -- "$CODE_VIEW_SOURCE_ROOT" && pwd)"
elif [ ! -d "$INSTALL_ROOT/.git" ]; then
    if [ -e "$INSTALL_ROOT" ]; then
        printf 'Install directory exists but is not a Code View checkout: %s\n' "$INSTALL_ROOT" >&2
        exit 2
    fi
    mkdir -p "$(dirname -- "$INSTALL_ROOT")"
    printf 'Downloading Code View into %s\n' "$INSTALL_ROOT"
    git clone --depth 1 --branch "$CODE_VIEW_REF" "$CODE_VIEW_REPO_URL" "$INSTALL_ROOT"
fi

if [ ! -x "$INSTALL_ROOT/script/code-view-command.sh" ]; then
    printf 'Installed checkout is missing script/code-view-command.sh: %s\n' "$INSTALL_ROOT" >&2
    exit 2
fi

if [ -z "$BIN_DIR" ]; then
    if [ -n "$BREW" ]; then
        BIN_DIR="$($BREW --prefix)/bin"
    else
        BIN_DIR="$HOME_DIR/.local/bin"
    fi
fi
mkdir -p "$BIN_DIR"
target="$BIN_DIR/code-view"
if [ -e "$target" ] && ! grep -q 'Code View installed launcher' "$target" 2>/dev/null; then
    printf 'Refusing to replace an existing command: %s\n' "$target" >&2
    exit 2
fi
temporary="$target.tmp.$$"
printf '%s\n' '#!/bin/sh' '# Code View installed launcher' "exec \"$INSTALL_ROOT/script/code-view-command.sh\" \"\$@\"" > "$temporary"
chmod 755 "$temporary"
mv "$temporary" "$target"

printf '%s\n' 'Code View installed.'
printf 'Run `code-view` from any Python repository.\n'
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'If the command is not found, add this directory to PATH: %s\n' "$BIN_DIR" ;;
esac

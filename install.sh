#!/bin/sh
set -eu

CODE_VIEW_REPO_URL="${CODE_VIEW_REPO_URL:-https://github.com/Rkj0123/code-view.git}"
CODE_VIEW_REF="${CODE_VIEW_REF:-main}"
HOME_DIR="${HOME:?HOME is required}"
INSTALL_ROOT="${CODE_VIEW_INSTALL_ROOT:-$HOME_DIR/.local/share/code-view}"
BIN_DIR="${CODE_VIEW_BIN_DIR:-}"
SKIP_DEPENDENCIES="${CODE_VIEW_SKIP_DEPENDENCIES:-0}"
INVOKING_PATH=$PATH
RUNTIME_PATH=""

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
    # A fresh Homebrew install is not necessarily in this process's PATH.
    brew_environment=$("$BREW" shellenv)
    eval "$brew_environment"
    brew_prefix=$("$BREW" --prefix)
    RUNTIME_PATH="$brew_prefix/bin:$brew_prefix/sbin"
    usable() {
        command -v "$1" >/dev/null 2>&1 || return 1
        case "$1" in
            python3) python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ;;
            node) version=$(node --version) && printf '%s\n' "$version" | awk -F. '{ sub(/^v/, "", $1); exit !(($1 == 20) || ($1 == 22) || ($1 >= 24)) }' ;;
            cmake) version=$(cmake --version) && printf '%s\n' "$version" | awk 'NR == 1 { split($3, v, "."); exit !(v[1] > 3 || (v[1] == 3 && v[2] >= 21)) }' ;;
            *) "$1" --version >/dev/null 2>&1 ;;
        esac
    }
    for tool in cmake ninja node npm python3 git; do
        if ! usable "$tool"; then
            case "$tool" in
                python3) formula=python ;;
                npm) formula=node ;;
                *) formula=$tool ;;
            esac
            "$BREW" install "$formula"
            # Prefer the installed formula over an older executable on PATH.
            formula_path="$("$BREW" --prefix "$formula")/bin"
            PATH="$formula_path:$PATH"
            RUNTIME_PATH="$formula_path:$RUNTIME_PATH"
            export PATH
            if ! usable "$tool"; then
                printf 'Required tool is unavailable or too old after installing %s: %s\n' "$formula" "$tool" >&2
                exit 2
            fi
        fi
    done
    if ! "$BREW" list --formula qthttpserver >/dev/null 2>&1; then
        "$BREW" install qthttpserver
    elif "$BREW" outdated --quiet qthttpserver | grep -q .; then
        "$BREW" upgrade qthttpserver
    fi
fi

if [ -n "${CODE_VIEW_SOURCE_ROOT:-}" ]; then
    INSTALL_ROOT="$(CDPATH= cd -- "$CODE_VIEW_SOURCE_ROOT" && pwd)"
elif [ ! -d "$INSTALL_ROOT/.git" ]; then
    if [ -e "$INSTALL_ROOT" ] || [ -L "$INSTALL_ROOT" ]; then
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
        BIN_DIR="$("$BREW" --prefix)/bin"
    else
        BIN_DIR="$HOME_DIR/.local/bin"
    fi
fi
mkdir -p "$BIN_DIR"
target="$BIN_DIR/code-view"
if [ -L "$target" ] || { [ -e "$target" ] && {
    [ ! -f "$target" ] || [ "$(sed -n '2p' "$target")" != '# Code View installed launcher' ];
}; }; then
    printf 'Refusing to replace an existing command: %s\n' "$target" >&2
    exit 2
fi
# Build successfully before creating or replacing the installed command.
if [ "${CODE_VIEW_SKIP_BUILD:-0}" != "1" ]; then
    "$INSTALL_ROOT/script/code-view.sh" --build-only
fi

# Single-quote the path as shell data, including literal quotes and newlines.
quoted_root=$(printf '%s' "$INSTALL_ROOT/script/code-view-command.sh" | sed "s/'/'\\\\''/g")
quoted_runtime_path=$(printf '%s' "$RUNTIME_PATH" | sed "s/'/'\\\\''/g")
quoted_python=$(command -v python3 | sed "s/'/'\\\\''/g")
temporary=$(mktemp "$BIN_DIR/.code-view.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
{
    printf '%s\n' '#!/bin/sh' '# Code View installed launcher'
    if [ -n "$RUNTIME_PATH" ]; then
        printf '%s\n' "export PATH='$quoted_runtime_path':\"\$PATH\""
    fi
    printf '%s\n' "export CODE_VIEW_PYTHON='$quoted_python'"
    printf '%s\n' "exec '$quoted_root' \"\$@\""
} > "$temporary"
chmod 755 "$temporary"
mv -f "$temporary" "$target"

printf '%s\n' 'Code View installed.'
printf 'Run `code-view` from any Python repository.\n'
case ":$INVOKING_PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        quoted_bin=$(printf '%s' "$BIN_DIR" | sed "s/'/'\\\\''/g")
        printf 'Run this once in your terminal to enable code-view:\nexport PATH=%s:"$PATH"\n' "'$quoted_bin'"
        ;;
esac

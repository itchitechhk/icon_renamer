#!/bin/bash
# Icon Renamer Launcher for macOS
# Picks a Python with a modern Tk, recreates .venv when broken, then runs the app.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/icon_renamer.py"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
BREW_BIN="$(command -v brew 2>/dev/null || true)"

echo "=== Icon Batch Renamer Launcher ==="

if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "Error: icon_renamer.py not found."
    echo "Keep this .sh file in the same folder as icon_renamer.py."
    exit 1
fi

# Return 0 if this interpreter has a usable Tk for modern macOS (not Apple's Tk 8.5).
python_has_good_tk() {
    local py="$1"
    [ -x "$py" ] || return 1
    "$py" - <<'PY' 2>/dev/null
import sys
try:
    import tkinter
    import _tkinter
except Exception:
    raise SystemExit(1)
# Apple Xcode/CLT Python ships ancient Tk 8.5 which breaks UI on modern macOS.
ver = getattr(tkinter, "TkVersion", 0) or 0
if float(ver) < 8.6:
    raise SystemExit(2)
print(f"{sys.version.split()[0]} Tk {ver}")
raise SystemExit(0)
PY
}

# Collect candidate Python binaries, Homebrew first.
collect_python_candidates() {
    local cands=()
    local p

    if [ -n "$BREW_BIN" ]; then
        local brew_prefix
        brew_prefix="$("$BREW_BIN" --prefix 2>/dev/null || true)"
        for ver in 3.14 3.13 3.12 3.11 3.10 3.9; do
            for p in \
                "$brew_prefix/opt/python@$ver/bin/python$ver" \
                "$brew_prefix/bin/python$ver"
            do
                [ -x "$p" ] && cands+=("$p")
            done
        done
        for p in "$brew_prefix/bin/python3" "$brew_prefix/bin/python"; do
            [ -x "$p" ] && cands+=("$p")
        done
    fi

    for p in \
        "$(command -v python3.14 2>/dev/null || true)" \
        "$(command -v python3.13 2>/dev/null || true)" \
        "$(command -v python3.12 2>/dev/null || true)" \
        "$(command -v python3.11 2>/dev/null || true)" \
        "$(command -v python3.10 2>/dev/null || true)" \
        "$(command -v python3 2>/dev/null || true)"
    do
        [ -n "$p" ] && [ -x "$p" ] && cands+=("$p")
    done

    # Deduplicate while keeping order
    local seen="" out=()
    for p in "${cands[@]}"; do
        case " $seen " in
            *" $p "*) ;;
            *)
                seen+=" $p"
                out+=("$p")
                ;;
        esac
    done
    printf '%s\n' "${out[@]}"
}

# Try brew install python-tk for a candidate version.
try_install_python_tk() {
    local py="$1"
    [ -n "$BREW_BIN" ] || return 1
    local minor
    minor="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    [ -n "$minor" ] || return 1

    echo "Trying to install Homebrew python-tk@${minor} ..."
    if "$BREW_BIN" install "python-tk@${minor}"; then
        python_has_good_tk "$py"
        return $?
    fi
    return 1
}

find_base_python() {
    local py info
    local -a cands=()
    while IFS= read -r py; do
        [ -n "$py" ] && cands+=("$py")
    done < <(collect_python_candidates)

    if [ ${#cands[@]} -eq 0 ]; then
        return 1
    fi

    for py in "${cands[@]}"; do
        if info="$(python_has_good_tk "$py")"; then
            BASE_PYTHON="$py"
            BASE_PYTHON_INFO="$info"
            return 0
        fi
    done

    # Auto-fix: install python-tk for the newest Homebrew Python we found.
    for py in "${cands[@]}"; do
        case "$py" in
            */Cellar/*|*/opt/python@*|*/homebrew/*|*/linuxbrew/*) ;;
            *)
                # Also accept paths under brew prefix
                if [ -n "$BREW_BIN" ]; then
                    local bp
                    bp="$("$BREW_BIN" --prefix 2>/dev/null || true)"
                    case "$py" in
                        "$bp"/*) ;;
                        *) continue ;;
                    esac
                else
                    continue
                fi
                ;;
        esac
        if try_install_python_tk "$py"; then
            info="$(python_has_good_tk "$py" || true)"
            BASE_PYTHON="$py"
            BASE_PYTHON_INFO="${info:-unknown}"
            return 0
        fi
    done

    return 1
}

venv_needs_rebuild() {
    if [ ! -x "$VENV_PYTHON" ]; then
        return 0
    fi
    # Broken / ancient Tk, or venv not based on our chosen interpreter.
    if ! python_has_good_tk "$VENV_PYTHON" >/dev/null; then
        return 0
    fi
    # Rebuild if venv was created from a different base interpreter.
    local base_home venv_base
    base_home="$("$BASE_PYTHON" -c 'import sys; print(sys.prefix)' 2>/dev/null || true)"
    venv_base="$("$VENV_PYTHON" -c 'import sys; print(getattr(sys, "base_prefix", sys.prefix))' 2>/dev/null || true)"
    if [ -n "$base_home" ] && [ -n "$venv_base" ]; then
        case "$venv_base" in
            "$base_home"|"$base_home"/*) return 1 ;;
            *) return 0 ;;
        esac
    fi
    return 1
}

BASE_PYTHON=""
BASE_PYTHON_INFO=""

if ! find_base_python; then
    echo "Error: no usable Python + Tk found (need Tk 8.6+)."
    echo "The system /usr/bin/python3 ships Tk 8.5, which breaks the UI on modern macOS."
    if [ -n "$BREW_BIN" ]; then
        echo "Run:"
        echo "  brew install python@3.14 python-tk@3.14"
    else
        echo "Install Homebrew first, then run:"
        echo "  brew install python@3.14 python-tk@3.14"
    fi
    exit 1
fi

echo "Using Python: $BASE_PYTHON ($BASE_PYTHON_INFO)"

if venv_needs_rebuild; then
    if [ -d "$VENV_DIR" ]; then
        echo "Detected old/broken .venv (often Apple Tk 8.5); rebuilding..."
        rm -rf "$VENV_DIR"
    else
        echo "Creating virtual environment .venv ..."
    fi
    "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

echo "Checking and installing dependencies..."
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install requests tkinterdnd2 -q

if ! python_has_good_tk "$VENV_PYTHON" >/dev/null; then
    echo "Error: virtual environment still lacks a modern Tk."
    echo "Delete .venv and retry, or run: brew install python@3.14 python-tk@3.14"
    exit 1
fi

echo "Starting app..."
echo "----------------------------------------"
exec "$VENV_PYTHON" "$PYTHON_SCRIPT"

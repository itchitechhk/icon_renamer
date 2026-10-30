#!/bin/bash
# Icon Renamer Launcher for macOS
# Creates a local venv, installs dependencies if needed, then runs the app.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/icon_renamer.py"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

echo "=== Icon Batch Renamer Launcher ==="

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "錯誤：找不到 python3。請先安裝 Python 3。"
    echo "建議使用 Homebrew：brew install python"
    exit 1
fi

echo "使用 Python: $(python3 --version)"

# Create venv if missing (needed on Homebrew Python / PEP 668)
if [ ! -x "$VENV_PYTHON" ]; then
    echo "建立虛擬環境 .venv ..."
    python3 -m venv "$VENV_DIR"
fi

# Install required packages into venv
echo "檢查並安裝依賴套件..."
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install requests tkinterdnd2 -q

# Verify tkinter is available
if ! "$VENV_PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "錯誤：找不到 tkinter（_tkinter）。"
    PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "請先安裝：brew install python-tk@${PY_MINOR}"
    exit 1
fi

# Check if the Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "錯誤：找不到 icon_renamer.py"
    echo "請確認此 .sh 檔與 icon_renamer.py 在同一個資料夾。"
    exit 1
fi

echo "啟動程式..."
echo "----------------------------------------"
"$VENV_PYTHON" "$PYTHON_SCRIPT"

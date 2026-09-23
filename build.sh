#!/bin/bash
# ========================================================
# DeckCastDJ - macOS & Linux Standalone Build Script
# ========================================================

set -e

# Change directory to project root
cd "$(dirname "$0")"

# Parse command-line arguments
CLEAN_BUILD=0
REFRESH_CACHE=0

for arg in "$@"; do
    case "$arg" in
        -clean|--clean|clean)
            CLEAN_BUILD=1
            ;;
        -refresh|--refresh|refresh)
            REFRESH_CACHE=1
            ;;
    esac
done

echo "========================================================"
if [ "$CLEAN_BUILD" -eq 1 ]; then
    echo "Rebuilding DeckCastDJ Standalone Executable [CLEAN DE-PERSONALIZED MODE]"
else
    echo "Rebuilding DeckCastDJ Standalone Executable [DEVELOPER MODE]"
fi
echo "========================================================"

# 1. Detect Python command (python3 or python)
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "[ERROR] Python was not found in your PATH."
    exit 1
fi

echo "Using Python: $($PYTHON --version) ($PYTHON)"

# 2. Check if PyInstaller is installed
if ! $PYTHON -m PyInstaller --version &>/dev/null; then
    echo "[ERROR] PyInstaller is not installed in your Python environment."
    echo "Install it with: pip install pyinstaller"
    exit 1
fi

# Ensure running instance is closed before rebuilding
pkill -f "DeckCastDJ" 2>/dev/null || true

# 3. Build to temporary directory to avoid cloud sync file locks
STAGING_DIST="/tmp/deckcast_dist"
STAGING_WORK="/tmp/deckcast_work"
rm -rf "$STAGING_WORK" "$STAGING_DIST"

echo "Running PyInstaller..."
$PYTHON -m PyInstaller DeckCastDJ.spec \
    --distpath "$STAGING_DIST" \
    --workpath "$STAGING_WORK" \
    --clean \
    --noconfirm

echo ""
echo "========================================================"
echo "Syncing compiled binaries and assets to dist/DeckCastDJ..."
echo "========================================================"

mkdir -p "dist/DeckCastDJ"

# Copy binary & _internal
cp "$STAGING_DIST/DeckCastDJ/DeckCastDJ" "dist/DeckCastDJ/DeckCastDJ"
chmod +x "dist/DeckCastDJ/DeckCastDJ"

rm -rf "dist/DeckCastDJ/_internal"
cp -R "$STAGING_DIST/DeckCastDJ/_internal" "dist/DeckCastDJ/_internal"

# Copy/Update root assets into dist/DeckCastDJ/
if [ "$CLEAN_BUILD" -eq 1 ]; then
    echo ""
    echo "--------------------------------------------------------"
    echo "[CLEAN MODE] Preparing de-personalized data & config..."
    echo "--------------------------------------------------------"
    rm -rf "dist/DeckCastDJ/data"
    
    REFRESH_ARG=""
    if [ "$REFRESH_CACHE" -eq 1 ]; then
        REFRESH_ARG="--refresh"
    fi
    
    $PYTHON prepare_clean_distro.py --dest "dist/DeckCastDJ/data" --config-dest "dist/DeckCastDJ/config.py" $REFRESH_ARG
else
    if [ ! -f "dist/DeckCastDJ/config.py" ]; then
        cp "config.py" "dist/DeckCastDJ/config.py"
    fi

    if [ ! -d "dist/DeckCastDJ/data" ]; then
        cp -R "data" "dist/DeckCastDJ/data"
    fi
fi

# Always update templates and static to match latest edits
rm -rf "dist/DeckCastDJ/templates" "dist/DeckCastDJ/static"
cp -R "templates" "dist/DeckCastDJ/templates"
cp -R "static" "dist/DeckCastDJ/static"

# 4. Check for ffmpeg
if [ -f "ffmpeg" ]; then
    cp "ffmpeg" "dist/DeckCastDJ/ffmpeg"
    chmod +x "dist/DeckCastDJ/ffmpeg"
elif command -v ffmpeg &>/dev/null; then
    echo "Found system ffmpeg at: $(command -v ffmpeg)"
else
    echo "[NOTE] ffmpeg not found in PATH. Track extraction works best when ffmpeg is installed (brew install ffmpeg)."
fi

# 5. Create 1-click double-clickable macOS launcher (run.command)
cat << 'EOF' > "dist/DeckCastDJ/run.command"
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "Starting DeckCastDJ..."
./DeckCastDJ &
PID=$!
sleep 2
open "http://localhost:5054"
wait $PID
EOF

chmod +x "dist/DeckCastDJ/run.command"

echo ""
echo "========================================================"
echo "[SUCCESS] DeckCastDJ macOS Release built successfully!"
echo "Output folder: dist/DeckCastDJ/"
echo "To run on Mac: Double-click dist/DeckCastDJ/run.command"
echo "========================================================"

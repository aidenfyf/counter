#!/bin/bash
# One-shot installer: schedules the refresh and installs the desktop widget.
#
#   ./install.sh
#
# Safe to re-run; it replaces what it installed last time.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.claude-counter"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
WIDGETS="$HOME/Library/Application Support/Übersicht/widgets"

echo "counter: installing from $DIR"

command -v python3 >/dev/null || { echo "need python3"; exit 1; }
python3 -c "import yaml" 2>/dev/null || { echo "need pyyaml:  pip3 install pyyaml"; exit 1; }
python3 -c "import PIL"  2>/dev/null || echo "  note: pillow missing, render checks will be skipped (pip3 install pillow)"

# 1. first run, so there is something to show
"$DIR/execution/refresh.sh" || true

# 2. schedule it
sed "s|__COUNTER_DIR__|$DIR|g" "$DIR/com.claude-counter.plist" > "$AGENT"
launchctl unload "$AGENT" 2>/dev/null || true
launchctl load "$AGENT"
echo "  scheduled: $LABEL (every 10 min)"

# 3. desktop widget, if Übersicht is installed
if [ -d "$WIDGETS" ]; then
  sed "s|__COUNTER_DIR__|$DIR|g" "$DIR/widgets/claude-counter.jsx" > "$WIDGETS/claude-counter.jsx"
  cp "$DIR/out/card.png" "$WIDGETS/claude-counter.png" 2>/dev/null || true
  # dragging needs interaction mode; harmless if already set
  defaults write tracesOf.Uebersicht enableInteraction -bool true
  echo "  desktop widget installed (restart Übersicht to pick up interaction)"
else
  echo "  Übersicht not found - skipping desktop widget (brew install --cask ubersicht)"
fi

echo
echo "done. Card: $DIR/out/card.png"
echo "For the iPhone widget see README.md > Phone."

#!/usr/bin/env bash
cd "$(dirname "$0")"

LOG_FILE="$(pwd)/download.log"

if [[ ! -f "$LOG_FILE" ]]; then
  echo "No log yet. Start the download first:"
  echo "  ./download-mosh-course.sh"
  exit 1
fi

echo "Watching: $LOG_FILE"
echo "Press Ctrl+C to stop watching (download keeps running)."
echo ""
tail -f "$LOG_FILE"

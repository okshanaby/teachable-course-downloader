#!/usr/bin/env bash
# Bulk-download a Code With Mosh (Teachable) course into the current directory.

set -euo pipefail
cd "$(dirname "$0")"

# Course page used to scrape the full curriculum (sections + lectures)
COURSE_URL="https://members.codewithmosh.com/courses/build-ai-powered-apps"

# Teachable SSO login (email + OTP)
LOGIN_URL="https://sso.teachable.com/secure/146684/identity/login/otp"

# After login, open the course — any URL under this path triggers the download
MAN_LOGIN_URL="https://members.codewithmosh.com/courses/build-ai-powered-apps"

LOG_FILE="$(pwd)/download.log"

if [[ ! -d env ]]; then
  echo "Virtual env missing. Run: python3 -m venv env && source env/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source env/bin/activate

clear
echo "=============================================="
echo " Code With Mosh — bulk course download"
echo "=============================================="
echo ""
echo "IMPORTANT: Keep this terminal open while downloading."
echo "Chrome will steal focus — that is normal."
echo ""
echo "Progress log file:"
echo "  $LOG_FILE"
echo ""
echo "To watch progress in a SECOND terminal, run:"
echo "  tail -f \"$LOG_FILE\""
echo ""
echo "Or run:  ./watch-progress.sh"
echo ""
echo "Steps in the browser:"
echo "  1. Enter your email on the login page"
echo "  2. Enter the OTP code sent to your email"
echo "  3. Open Build AI-Powered Apps (URL must contain build-ai-powered-apps)"
echo ""
echo "Files save to: $(pwd)/courses/<course-name>/"
echo ""
echo "Auto-resume is ON (skips finished lectures)."
echo "Progress file: $(pwd)/download-progress.json"
echo "To start fresh: ./download-mosh-course.sh --no-resume"
echo ""
echo "Starting in 3 seconds..."
sleep 3

RESUME_FLAG=""
if [[ "${1:-}" == "--no-resume" ]]; then
  RESUME_FLAG="--no-resume"
  echo "Re-downloading everything (--no-resume)."
fi

python3 main.py \
  --url "$COURSE_URL" \
  --login_url "$LOGIN_URL" \
  --man_login_url "$MAN_LOGIN_URL" \
  --log-file "$LOG_FILE" \
  $RESUME_FLAG \
  -vv

echo ""
echo "Done. Full log saved to: $LOG_FILE"

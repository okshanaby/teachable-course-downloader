# Teachable Course Downloader

Bulk-download videos and PDF attachments from [Teachable](https://teachable.com)-hosted membership sites (e.g. [Code With Mosh](https://members.codewithmosh.com)).

Forked from [FallingLights/Teachable-dl](https://github.com/FallingLights/Teachable-dl) with improvements for OTP login, short videos, PDF attachments, auto-resume, and progress logging.

## Features

- Download all lectures in a course (videos + PDFs), organized by section
- **Auto-resume** — skips completed lectures; tracks progress in `download-progress.json`
- OTP / manual login support (no password in shell history)
- Pauses the video player before extracting download URLs (fixes short lecture failures)
- Cleans partial downloads on retry
- Progress log file + `watch-progress.sh` helper

## Requirements

- Python 3.10+
- Google Chrome or Chromium
- ffmpeg
- wget

## Setup

```bash
git clone https://github.com/okshanaby/teachable-course-downloader.git
cd teachable-course-downloader

python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Code With Mosh (quick start)

```bash
./download-mosh-course.sh
```

1. Chrome opens on the Teachable SSO login page
2. Enter your email and OTP
3. Open your course (URL must contain `build-ai-powered-apps` for the default script)
4. Downloads start automatically

Watch progress in a second terminal:

```bash
./watch-progress.sh
# or: tail -f download.log
```

Files are saved under `courses/<Course-Name>/<Section>/`.

## Generic Teachable course

```bash
source env/bin/activate

python3 main.py \
  --url "https://yourschool.teachable.com/courses/your-course" \
  --login_url "https://sso.teachable.com/secure/SCHOOL_ID/identity/login/otp" \
  --man_login_url "https://yourschool.teachable.com/courses/your-course" \
  --log-file download.log \
  -vv
```

Log in manually in the browser, navigate to the course page, and downloading begins when the URL matches.

## Resume

Auto-resume is **on by default**. Re-run the same command after a crash or Ctrl+C — finished lectures are skipped.

```bash
./download-mosh-course.sh              # resume
./download-mosh-course.sh --no-resume  # re-download everything
```

## Options

```bash
python3 main.py --help
```

| Flag | Description |
|------|-------------|
| `--no-resume` | Disable auto-resume |
| `--log-file PATH` | Write logs to a file |
| `-vv` | Verbose output |
| `-e` / `-p` | Email/password login (optional) |

## What is not committed

These are gitignored and stay on your machine only:

- `courses/` — downloaded videos and PDFs
- `env/` — Python virtual environment
- `download-progress.json` — local resume state
- `download.log` — run logs

## License

Based on [Teachable-dl](https://github.com/FallingLights/Teachable-dl) (LGPL-3.0). See [LICENSE.txt](LICENSE.txt).

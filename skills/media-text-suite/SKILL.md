---
name: media-text-suite
description: Download media from a URL, transcribe local or downloaded audio/video, or summarize transcript text using the local Media Text Suite installation. Use when the user gives media links or asks to process media with this installed tool.
---

# Media Text Suite

Use the local Media Text Suite command-line interface for media work. The desktop application and browser extension are independent; this skill calls the desktop pipeline directly and does not require the browser extension to be loaded.

## Run

Use `scripts/run_media_task.py` with `--action` and the relevant inputs. The helper locates the installation from `MEDIA_TEXT_SUITE_HOME`, defaulting to the repository's `source` directory. If the Skill has been installed separately and cannot locate the checkout, pass `--suite-root` or ask for the source directory. It invokes `desktop/agent_cli.py`; the Python dependencies from `source/requirements.txt` must be installed.

Actions:

- `download`: download the video (not audio-only) from one or more `--url` values.
- `transcribe`: transcribe a local media file with `--file`.
- `summarize`: summarize provided transcript text using `--text` or a text file using `--transcript`.
- `download-transcribe`: download and transcribe one or more URLs.
- `download-transcribe-summarize`: perform all three steps; MiniMax API credentials must already be saved in app settings or supplied with `--api-key`.

For mixed batches, call once per requested workflow or use a combined action. Pass repeated `--url` arguments for multiple links. Do not claim a task succeeded unless the returned JSON has `ok: true`; relay output file paths, errors, and per-stage timings. The command returns JSON on stdout and a nonzero exit code if any batch item fails.

## Example

```powershell
python scripts\run_media_task.py --action download-transcribe --url "https://example.com/video"
```

Install this Skill into the current user's Codex skills folder from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File skills\install_skill.ps1
```

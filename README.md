# Media Transcriber Suite

Windows desktop app for downloading media, transcribing audio/video, and summarizing transcripts with MiniMax. The `source/extension` folder contains the independent browser download extension.

## Run the packaged app

Download the latest `MediaTranscriberSuite-*-win64.zip` from the [Releases](https://github.com/puppy-bai/MediaTranscriberSuite/releases) page and unpack it anywhere. Then run `start_media_suite.bat` or launch `MediaTranscriberSuite\MediaTranscriberSuite.exe` directly. Keep the entire `MediaTranscriberSuite` folder together; the executable needs the adjacent `_internal` directory.

The first transcription may download a speech-recognition model. CUDA libraries are optional; without a compatible CUDA runtime, transcription uses CPU.

## Build from source

Install Python 3.13 or 3.14, then run in PowerShell:

```powershell
cd source
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

The build script writes the onedir package to `../release/candidate/MediaTranscriberSuite`. The packaged folder is not tracked in git; shipping builds are attached to GitHub Releases instead.

## Agent CLI

From `source`, call `python desktop/agent_cli.py --help` for actions and options. Example:

```powershell
python desktop/agent_cli.py --action download-transcribe --url "https://example.com/video"
```

## Codex Skill

The `skills/media-text-suite` directory contains an installable Codex Skill and its runner. From PowerShell at the repository root, install it with:

```powershell
powershell -ExecutionPolicy Bypass -File skills/install_skill.ps1
```

Restart or refresh Codex after installation, then invoke `$media-text-suite` with a media link. The installer points the Skill at this checkout's `source` directory. If the repository is moved later, rerun the installer or set `MEDIA_TEXT_SUITE_HOME` to the new `source` directory.

## Privacy and data

Settings, API keys, download history, transcripts, and media files are created locally under the app's `data` directory and are excluded from this repository. Enter your MiniMax API key in the app settings.

## Repository layout

- `source/`: Python application, agent CLI, independent browser extension, and build scripts.
- `skills/media-text-suite/`: installable Skill for Codex and compatible agent workflows.
- `MediaTranscriberSuite/`: Windows onedir distribution (build output, not tracked in git). It is published as a zip on the Releases page; unpack the whole folder, including `_internal`.
- `start_media_suite.bat`: launches the packaged app.

Use only for media you have permission to access and download, and follow each platform's terms.

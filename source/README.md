# Media Text Suite

Windows desktop app and local AI-agent interface for downloading, transcribing, and summarizing media.

## Start the desktop app

Run `D:\MediaTranscriberSuite_Workspace\start_media_suite.bat` or from this folder:

```powershell
python desktop\app.py
```

## Agent interface

Run from `source` so Python can import the local core modules:

```powershell
python desktop\agent_cli.py --action download-transcribe-summarize --url "https://example.com/video"
python desktop\agent_cli.py --action transcribe --file "D:\media\video.mp4"
python desktop\agent_cli.py --action summarize --transcript "D:\media\transcript.txt"
```

Every invocation emits one JSON object on stdout; a failed batch returns exit code 1. MiniMax credentials may be supplied with `--api-key` or saved in `desktop\data\settings.json` by the desktop app.

## Dependencies and packaging

Install dependencies with `python -m pip install -r requirements.txt`, then package with `powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1`.

The browser extension in `extension` remains independent and only captures/downloads media URLs.

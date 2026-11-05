import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def default_suite_root():
    if os.environ.get("MEDIA_TEXT_SUITE_HOME"):
        return os.environ["MEDIA_TEXT_SUITE_HOME"]
    repository_root = Path(__file__).resolve().parents[3]
    return str(repository_root / "source")


def main():
    parser = argparse.ArgumentParser(description="Run a Media Text Suite task and emit its JSON result")
    parser.add_argument("--suite-root", default=default_suite_root())
    parser.add_argument("--action", required=True, choices=("download", "transcribe", "summarize", "download-transcribe", "download-transcribe-summarize"))
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--file")
    parser.add_argument("--transcript")
    parser.add_argument("--text")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cuda", "cpu"))
    parser.add_argument("--compute-type")
    parser.add_argument("--beam-size", type=int)
    parser.add_argument("--summary-mode")
    args = parser.parse_args()
    root = Path(args.suite_root).resolve()
    cli = root / "desktop" / "agent_cli.py"
    if not cli.is_file():
        parser.error(f"Media Text Suite source not found: {root}")
    candidates = []
    local_python = root / ".venv" / "Scripts" / "python.exe"
    if local_python.is_file():
        candidates.append([str(local_python)])
    candidates.append([sys.executable])
    if os.name == "nt" and shutil.which("py"):
        candidates.extend((["py", "-3.13"], ["py", "-3.14"], ["py", "-3.12"]))
    python_command = candidates[0]
    for candidate in candidates:
        check = subprocess.run([*candidate, "-c", "import yt_dlp, faster_whisper, openai"], capture_output=True, text=True)
        if check.returncode == 0:
            python_command = candidate
            break
    command = [*python_command, str(cli), "--action", args.action]
    for name in ("url", "file", "transcript", "text", "api_key", "model", "device", "compute_type", "beam_size", "summary_mode"):
        value = getattr(args, name)
        if isinstance(value, list):
            for item in value:
                command.extend(("--url", item))
        elif value is not None:
            command.extend(("--" + name.replace("_", "-"), str(value)))
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

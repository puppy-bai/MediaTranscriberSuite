# Media Transcriber Suite（媒体转写套件）

Windows 桌面应用，用于下载媒体、转写音/视频，并用 MiniMax 汇总转写文本。`source/extension` 目录内是独立的浏览器下载插件。

> [English](./README.md) | **简体中文**

## 运行打包好的应用

从 [Releases](https://github.com/puppy-bai/MediaTranscriberSuite/releases) 页面下载最新的 `MediaTranscriberSuite-*-win64.zip` 并解压到任意目录。然后运行 `start_media_suite.bat`，或直接启动 `MediaTranscriberSuite\MediaTranscriberSuite.exe`。请保持整个 `MediaTranscriberSuite` 文件夹完整——可执行文件依赖同级的 `_internal` 目录。

首次转写可能需要下载语音识别模型。CUDA 库为可选；若没有兼容的 CUDA 运行环境，转写将回退到 CPU。

## 从源码构建

安装 Python 3.13 或 3.14，然后在 PowerShell 中运行：

```powershell
cd source
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

构建脚本会把 onedir 包输出到 `../release/candidate/MediaTranscriberSuite`。打包产物不纳入 git 管理；发布的构建版本以 GitHub Releases 附件形式提供。

### 在 GitHub Actions 上自动构建（推荐）

仓库里已包含 [`.github/workflows/build.yml`](.github/workflows/build.yml)。当你推送一个以 `v` 开头的 tag（如 `v1.1.0`）时，GitHub Actions 会在 Windows 上进行自动化构建，并把 zip 作为 Release 附件上传。

```powershell
git tag v1.1.0
git push origin v1.1.0
```

## Agent CLI

从 `source` 目录执行 `python desktop/agent_cli.py --help` 可查看操作与选项。示例：

```powershell
python desktop/agent_cli.py --action download-transcribe --url "https://example.com/video"
```

## Codex Skill

`skills/media-text-suite` 目录包含一个可安装的 Codex Skill 及其运行脚本。在仓库根目录的 PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File skills/install_skill.ps1
```

安装后重启或刷新 Codex，即可用 `$media-text-suite` 传入媒体链接调用。安装脚本会把该 Skill 指向本仓库的 `source` 目录；若日后移动仓库位置，请重新运行安装器，或把 `MEDIA_TEXT_SUITE_HOME` 设置为新的 `source` 目录。

## 隐私与数据

设置、API 密钥、下载历史、转写文本与媒体文件均保存在应用本地的 `data` 目录下，且被排除在仓库之外。请在应用设置中填写你的 MiniMax API 密钥。

## 仓库结构

- `source/`：Python 应用、Agent CLI、独立浏览器插件与构建脚本。
- `skills/media-text-suite/`：可安装的 Codex Skill（含运行脚本）。
- `.github/workflows/build.yml`：打 tag 时的自动化构建工作流。
- `MediaTranscriberSuite/`：Windows onedir 分发目录（构建产物，不入 git）；以 zip 形式发布到 Releases，解压时请包含整个文件夹，含 `_internal`。
- `start_media_suite.bat`：启动打包应用。

仅对你有权访问和下载的媒体使用本工具，并遵守各平台的使用条款。
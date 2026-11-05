# Release Publisher（发布管理小工具）

一个**零第三方依赖**的桌面小工具（`tkinter` + Python 标准库），用来可视化地发布 GitHub Release 并上传文件（如打包好的 Windows zip）。

## 运行

```powershell
python release_tool\release_publisher.py
```

（需要 Python 3.10+；无需安装任何额外包。）

## 功能

- **选择文件**：通过系统文件对话框选择要上传的 zip / exe 等。
- **自定义发布信息**：仓库（owner/name）、Tag、Release 标题、Markdown 发布说明。
- **上传 Release 附件**：自动创建 Release（可选草稿）并上传所选文件，带实时进度。
- **软件内配置权限**：在界面里填写 GitHub 个人访问令牌（Token），可一键验证、可选“显示”，本地保存（隐藏文件，仅当前用户可读），不必每次手输。

## Token 权限说明

创建 Release 并上传附件需要具有 `repo` 权限的令牌。在界面内填写的 Token 会保存在用户主目录下的 `.media_transcriber_release_tool\config.json`。请勿把 Token 提交进 git。

> 提示：本项目另有 `.github/workflows/build.yml`，可在推送 `v*` tag 时由 GitHub Actions 自动构建并上传发布，无需本工具。
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
- **软件内填写凭据**：在界面里填写 GitHub 个人访问令牌（Token），可一键验证、可选“显示”。Token 仅在本次运行时保留在内存中，不写入配置文件。GitHub 权限需在 GitHub 网站创建令牌时授予，软件不能自行修改。

## Token 权限说明

创建 Release 并上传附件需要 classic token 的 `repo` 权限；细粒度 token 要选定目标仓库，并授予 **Contents: Read and write**。工具只在用户主目录的 `.media_transcriber_release_tool/config.json` 保存非敏感的仓库、Tag 和文件路径等偏好设置，**不会保存 Token**。如果使用过旧版工具，请删除该配置文件或打开新版工具后关闭，以移除旧版留下的明文 Token。请勿把 Token 提交进 git。

> 提示：本项目另有 `.github/workflows/build.yml`，可在推送 `v*` tag 时由 GitHub Actions 自动构建并上传发布，无需本工具。
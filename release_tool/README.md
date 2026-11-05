# Release Publisher（可视化发布工具）

一个零第三方依赖的桌面小工具（Python 标准库 + tkinter），用于把构建好的 zip / exe 上传到 GitHub Release，
也可以在网页上手动发版之外，提供一套「点几下就完成」的本地流程。

![界面分区](#界面分区)

## 功能

- **多文件上传**：一次选多个文件（zip、exe 等），依次上传到同一个 Release。
- **权限自检**：填入 Token 后点「权限自检」，会真实做一次写入探测（创建无引用的 blob），
  明确告诉你：账号是谁、仓库可否访问、token 有没有写权限、缺哪个 scope。
- **网络模式**：跟随系统/环境变量、强制直连、或填写自定义代理（如 `http://127.0.0.1:7890`），
  校园网 / 公司网络下很实用。
- **Release 管理**：没有就创建，已存在就复用；可保存为草稿；可勾选「更新的 Release 说明」覆盖标题与正文。
- **上传进度**：实时显示百分比、已传/总量、速度与剩余秒数；随时可点「取消」。
- **Token 只在内存里**：不写入配置文件；若读到旧版本留下的明文 Token 会自动清除。
- **一键打开**：发布完成后直接跳到 Release 页面。

## 运行

需要 Python 3.10+（用到 `X | None` 类型语法）。

```powershell
python release_tool\release_publisher.py
```

## 打包成独立 exe（可选）

生成一个双击即可运行的 `ReleasePublisher.exe`：

```powershell
powershell -ExecutionPolicy Bypass -File release_tool\build_tool.ps1
```

产物：`release_tool\dist\ReleasePublisher.exe`（`dist/`、`build/` 已在 `.gitignore` 中）。

## Token 权限

- **Classic token**：勾选 `repo`；如果要推送 `.github/workflows/` 下的文件，再勾 `workflow`。
  发布 Release 只需要 `repo`。
- **Fine-grained token**：Repository access 选 `Only select repositories` 并勾选目标仓库；
  Permissions → Repository permissions → **Contents: Read and write**。

工具栏的「权限自检」会直接告诉你当前 token 属于哪一种、缺什么，不用再靠报错猜。

## 配置文件

偏好设置（仓库、Tag、窗口内容、文件列表、代理）保存在
`%USERPROFILE%\.media_transcriber_release_tool\config.json`，**不含 Token**。
如果使用过旧版本，打开新版后关闭一次即可清除历史明文 Token。

## 自测

```powershell
$env:PYTHONPATH="."
python release_tool\_selftest.py
```

覆盖：分块流式上传与 Content-Length、取消上传、权限诊断的判定逻辑。

> 提示：本项目另有 `.github/workflows/build.yml`，推送 `v*` tag 时由 GitHub Actions 自动构建并上传发布，
> 完全不需要本工具。

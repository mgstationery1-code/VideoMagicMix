# 构建、验收与发布

## 范围

当前发布只包含 v0.1.11。保留旧版本标签和 Release，不重新打包旧版，不提交用户素材。源码根目录为本仓库；应用源码、完整包和教程指向同一版本。

## 开发环境

Windows x64、Python 3.12 x64、Node.js 20。普通用户不需要这些依赖。

```powershell
npm ci
npm run package
```

构建脚本安装锁定 Python 依赖，获取并校验模型/媒体工具/字体，运行单元测试，冻结后端，生成 NSIS 安装包，执行离线 ASR/实际渲染/桌面冒烟测试，导出干净源码和完整目录 ZIP。

模型固定到 Hugging Face revision；FFmpeg 8.1.1 下载包固定 SHA256；HarmonyOS 字体使用固定版本镜像并验证 Git 对象哈希，保持原文件和许可。首次构建要联网，安装后本地识别不下载模型。

PyInstaller 在短临时路径生成，再复制到 build/backend，避免 Windows 深层路径导致缺 DLL/文件。脚本仅镜像生成的 build/backend 子目录。

## 独立测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test scripts/test-window-controls.cjs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/make_smoke_voice.ps1
.\.venv\Scripts\python.exe scripts/smoke_packaged.py dist/win-unpacked/resources/app-runtime --voice build/smoke-voice.wav --render
.\.venv\Scripts\python.exe scripts/smoke_desktop.py dist/win-unpacked/VideoMagicMix.exe
```

测试使用系统语音合成的非私人音频和合成视频，空 PATH、离线模型、临时数据目录。覆盖词级 ASR、导入/匹配、递归扫描、取消勾选、预渲染、试听、Hook 无 BGM、字幕预览、完整渲染与全片解码。
它证明技术链路，不保证所有业务录音都能识别准确。干净 GitHub Windows runner 与用户实际电脑是不同验收环境，不得混称。

## Release 附件

- `VideoMagicMix-Setup-0.1.11-x64.exe`：NSIS 安装版。
- `VideoMagicMix-Windows-0.1.11-x64.zip`：完整目录版；内含每文件校验清单。
- `VideoMagicMix-Source-0.1.11.zip`：干净源码，不能直接运行。
- `latest.yml`、安装包 `.blockmap`：Electron 自动更新使用。
- `SHA256SUMS.txt`：发布附件 SHA256。

资源体积较大，放 GitHub Releases，不放 Git 历史。仓库里的字体二进制由构建脚本获取，许可证和来源记录入库。

## 发布顺序

1. 核对源码与指定本地 v0.1.11 的 backend、frontend/app.js、desktop 行为一致，仅整理发布资源和说明。
2. 运行测试、核对无私人文件，提交源码并创建 v0.1.11 标签。
3. 创建 draft Release。运行 `Build Windows installer`，`release_tag` 填 v0.1.11。
4. 工作流在干净 Windows runner 构建验收，成功后将附件上传到草稿；失败不能发布。
5. 核对附件大小/哈希、最新更新清单、安装包版本；测试下载/解压/启动。只有完成要求的检查，才取消 draft 并设为 Latest。
6. 从公开 latest 链接核对下载；执行 INSTALL_FOR_CODEX 中的脚本验证。记录实际已完成和未完成的验收，不把待测写成通过。

标签 push 也会构建，但不自动发布。手动 release_tag 的上传不覆盖同名附件，已有附件需要维护者先核对，不能直接覆盖已发布版本。

## 许可证与数据

保留应用和第三方许可。FFmpeg 为 GPL-enabled 构建，附实际构建版本、编译信息和依赖源码获取说明；模型/字体也有独立许可。HarmonyOS 使用许可要求显著标注，本软件页脚提供字体声明和完整许可。

不发布个人 Excel、音频、视频、输出、日志、凭据、用户目录。发布前检查源码导出允许清单和目录包 FILE-MANIFEST.json。

v0.1.11 继续使用独立 `VideoMagicMix-balanced` 用户目录，不迁移/删除旧数据。项目状态只在内存中，教程应写明重启后重新导入。安装包未签名，不能声称已通过 SmartScreen 信任。

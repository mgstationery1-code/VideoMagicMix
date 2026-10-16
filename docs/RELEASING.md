# Windows 构建和发布

## VideoMatrix 实际做法

2026-10-16 核查上游 main：

- [Windows workflow](https://github.com/w2968066/VideoMatrix--/blob/main/.github/workflows/build-windows.yml)：安装依赖、获取 FFmpeg、PyInstaller 构建后端、Electron Builder 生成 NSIS、上传 Release。
- [后端 spec](https://github.com/w2968066/VideoMatrix--/blob/main/backend/videomatrix-backend.spec)：媒体工具及思源字体随独立后端分发。
- [桌面配置](https://github.com/w2968066/VideoMatrix--/blob/main/frontend/package.json)：Electron + React、NSIS 安装器、extraResources 带入后端。
- [后端依赖](https://github.com/w2968066/VideoMatrix--/blob/main/backend/requirements.txt)：没有 faster-whisper。传统混剪不需要本项目新增的语音识别模型。
- [v2.8.0](https://github.com/w2968066/VideoMatrix--/releases/tag/v2.8.0)：Windows 包 `VideoMatrix.Setup.2.8.0.exe`，278101388 字节；另有 Mac DMG 和更新清单。

该仓库有完整源码，不只是 skill。源码按模块区分，二进制和构建输出被忽略；也有历史单文件脚本和大量版本说明，不能据此断言每行代码都已审查。安装 skill 本身不会安装软件或模型。

本项目采用“源码仓库 + 独立后端 + Electron 安装包”，另外提供本地 ASR 模型。
PyInstaller 使用目录模式，避免每次启动识别进程都解压推理 DLL。
保持现有 frontend/desktop 分工，不为相同的目录名称重写界面框架。

## 构建

需要 Windows x64、Python 3.12、Node.js 20+、网络，在项目根目录执行：

```powershell
npm ci
npm run package
```

脚本建立虚拟环境、安装锁定依赖、准备和校验资产、跑测试、汇总许可、冻结后端、生成 NSIS，并检查独立后端和桌面启动。
模型约 461 MiB，FFmpeg 下载包约 104 MiB，构建缓存不进仓库。
只复制模型的四个必要文件，不复制 Hugging Face 缓存中的重复副本。

## 验收

```powershell
.\.venv\Scripts\python.exe scripts/smoke_packaged.py dist/win-unpacked/resources/app-runtime
# 冻结后的 ASR 子进程测试：
.\.venv\Scripts\python.exe scripts/smoke_packaged.py dist/win-unpacked/resources/app-runtime --voice "配音.wav"
# 完整 HTTP 导入/识别/预检/预览/单条渲染：
.\.venv\Scripts\python.exe scripts/smoke_packaged.py dist/win-unpacked/resources/app-runtime --voice "配音.wav" --render
# 桌面窗口及 preload 桥接：
.\.venv\Scripts\python.exe scripts/smoke_desktop.py dist/win-unpacked/VideoMagicMix.exe
```

测试使用独立临时数据、空 PATH、离线模型。提供真实配音时必须识别到词级时间戳；
`--render` 使用识别文本构建测试文案，只证明技术链路，不保证任意 Excel 与配音匹配。
测试输出独立保存，不混入正式产出。

正式发布前还需在无 Python/Node/FFmpeg 的另一台 Windows 10/11 x64 安装测试，
覆盖中文和空格路径、目录选择、真实业务录音、字幕位置和输出数量。
安装器未做代码签名；本机通过不能代替另一台机器验收。

2026-10-16 本机已验证：20 项单元测试通过；清空 PATH、离线模式下，冻结后端的词级 ASR、HTTP 导入/匹配/预检、内置思源黑体 ASS 预览与单条 1080×1920 带音轨输出通过；Electron 隐藏窗口加载及 preload 桥接通过。
完整链路测试使用 4 秒录音与合成画面；不代表所有真实长录音均能准确匹配。
最终 FFmpeg 8.1.1 资源包已验证 SHA256；完整应用复制到含中文和空格的新路径后，上述桌面、离线 ASR、预览和渲染测试再次通过。
本地已生成 NSIS 安装器 `VideoMagicMix-Setup-0.1.0-x64.exe`（657126952 字节），尚未在另一台电脑实际安装。
源码导出采用允许清单，已检查不包含用户素材、音频、成片、Excel、模型、EXE、DLL、虚拟环境或 node_modules。

同日 `npm audit` 对现有开发依赖报告 19 项（4 moderate / 14 high / 1 critical），涉及旧版 Electron 和构建依赖链。
本次未进行跨主版本升级，公开正式发布前需要单独升级和回归；不能把目前的打包验收当成完整安全审计。

## GitHub 分工

1. 以本项目源码目录作为仓库根目录，遵守 `.gitignore`，不上传整个 outputs 或 dist。
2. `scripts/export_source.py` 导出干净源码 ZIP，用于交接；它不是安装包。
3. 安装器及 SHA256 放 Releases，可另外提供 Portable EXE。
4. 保留第三方许可，发布前落实所用 FFmpeg 构建的 GPL 完整对应源码获取方式。
5. Actions 只构建并保留 artifact，不自动公开 Release；公开 Release 由维护者核验后手动创建。

用户素材、配音、成片、环境文件、日志、`.venv` 和开发机路径不随包分发。
项目/任务状态目前只存在内存中，重启后需要重新导入，尚不支持项目持久化。

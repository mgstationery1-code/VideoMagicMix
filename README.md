# VideoMagicMix

Windows 本地批量混剪工具，参考 [VideoMatrix](https://github.com/w2968066/VideoMatrix--) 的布局与发布方式。
工作流：完整 Hook → Excel 文案与 Body 专用配音 → 自动组合 Body → 逐句字幕 → 批量 MP4。
采用 Electron + FastAPI + FFmpeg + faster-whisper，不部署 NAS，不调用远程语音识别。

## 普通用户下载

Windows 10/11 x64 用户应到本项目的 **Releases** 下载
`VideoMagicMix-Setup-版本-x64.exe`，安装后启动软件。不要下载 `Code → Download ZIP` 作为安装包。

完整包包含独立 Python 后端、FFmpeg、FFprobe、Whisper small 模型与思源黑体。
无需另装 Python、Node.js、FFmpeg，也无需首次联网下载模型。默认 CPU int8 识别，不要求 NVIDIA 显卡。
`Code → Download ZIP` 是源码，需要开发环境。
`win-unpacked` 是完整目录版，必须保留所有文件，不能只复制其中的 EXE。

## 目录

```text
backend/       API、Excel、ASR、视频组合、字幕和渲染，含 PyInstaller spec
desktop/       Electron 主进程与安全 preload
frontend/      HTML / JavaScript / CSS 界面
tests/         业务逻辑与发布结构测试
scripts/       构建资产下载、打包、许可汇总、冒烟测试、源码导出
docs/          发布流程与参考项目核查
licenses/      第三方许可
fonts/         字体说明，构建时获取实际字体和 OFL 许可
.github/       Windows 自动构建
```

`.venv/`、`node_modules/`、`build/`、`dist/`、工具 EXE、模型、配音、素材和成片不提交 Git。

## 开发与打包

需要 Windows x64、Python 3.12 x64、Node.js 20+、网络，在本项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
npm ci
.\.venv\Scripts\python.exe scripts/prepare_assets.py
npm run desktop
```

构建时联网获取固定版本模型、字体与媒体工具；校验通过的已有文件复用。
模型和 FFmpeg 下载包校验 SHA256，字体固定到上游 Git commit。
下载失败即停止构建，不生成缺依赖的完整包。

```powershell
npm run package:dir       # dist/win-unpacked 完整目录版
npm run package           # dist/VideoMagicMix-Setup-0.1.0-x64.exe 安装版
npm run package:portable  # 单文件自解压便携版
.\.venv\Scripts\python.exe scripts/export_source.py
```

后端通过 PyInstaller 自带 Python 运行库，发布包不复制 `.venv`。
细节见 [发布说明](docs/RELEASING.md)。Actions 可手动或由 v* 标签触发，结果保留为 artifact，不自动公开发布。

## 使用

1. Excel A 列为 Hook、Body1 等，B 列分组名称，C 列以后每列一套文案。
2. Hook 文案忽略；目录递归读取完整带原声视频，不变速、不裁时间。
3. 每个 Body 独立选择素材目录，递归读取子目录；空文案按方案跳过。
4. 每方案上传一条只读启用 Body、顺序与 Excel 相同的配音，支持 MP3/WAV/M4A/AAC/FLAC/OGG/OPUS。
5. 上传并识别，确认匹配；调整各 Body 候选数量和独立阈值，预览字幕。
6. 预检后设置生成数量，0 表示全部已枚举可用组合，启动渲染。

最终音频为 Hook 原声接 Body 配音，Body 素材静音，软件字幕只加在 Body 上。
配音多读、漏读、内容不一致会阻止渲染；Whisper 不保证所有录音都识别准确。

## 数据与限制

桌面版上传、预览及默认输出在 Electron 用户数据目录的 data 下，手动输出目录优先。
项目和任务状态目前保存在内存中，退出后需重新导入，尚不支持持久化。
Body 每组最多搜索前 18 个视频，每组合最多 8 个文件，最多保留 500 个候选；预计产能是该搜索范围内的产能。
BGM、水印等历史界面项不代表所有效果已接入，当前验证重点是 Hook、Body、配音、字幕链路。
安装包尚无代码签名，Windows SmartScreen 可能提示未知发布者；另一台干净电脑仍需实际验收。

许可及第三方来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

# VideoMagicMix · v0.1.11

Windows 本地批量混剪软件：完整 Hook 开头接 Body 素材组合，使用你准备的配音，自动识别时间、生成逐句字幕、添加背景音乐并批量导出。

本次发布 **v0.1.11 均衡轮换版**，不重新打包或上传旧版。

## 新电脑直接使用

1. 打开 [最新版下载页](https://github.com/mgstationery1-code/VideoMagicMix/releases/latest)。
2. 下载 `VideoMagicMix-Setup-0.1.11-x64.exe` 并安装；或者下载 `VideoMagicMix-Windows-0.1.11-x64.zip`，完整解压后双击 `VideoMagicMix.exe`。
3. 按 [新手教程](docs/USER_GUIDE.md) 先生成 1–3 条测试视频，再批量生成。

适用 Windows 10/11 **x64**。完整包包含 Electron、Python 后端、FFmpeg、FFprobe、Whisper small 模型、思源字幕字体和 HarmonyOS 界面字体。下载后识别和渲染在本机运行，无需另装 Python、Node.js、FFmpeg，无需首次下载模型，不强制 NVIDIA 显卡。

**不要把 `Code → Download ZIP` 当安装包，不要只复制单个 EXE。** 建议预留至少 10 GB 空间供安装、缓存和测试，批量成片需要更多空间。CPU 识别速度随硬件和音频长度变化。

让另一台电脑的 Codex 协助安装：[INSTALL_FOR_CODEX.md](INSTALL_FOR_CODEX.md)。无需安装 skill。

## 本版功能

- Hook 递归扫描，完整播放、保留原声和已有字幕，不变速、不加 BGM。
- Excel A 列写 Hook、Body1、Body2……；B 列写素材分组路径；C 列以后每列一套文案。空 Body 按方案跳过，Hook 文案忽略。
- Body 根目录按 `开头/刻字` 自动匹配子文件夹，也支持逐组指定。
- 每方案上传一条只朗读启用 Body 的完整配音，词级时间决定逐句字幕。
- Body 默认 0.9–1.4 倍速内的候选；可调整数量/范围、排除组合、预渲染勾选片段。
- BGM 仅用于 Body，按配音长度裁剪或循环。Hook、配音和 BGM 音量独立，支持片段试听。
- 默认生成 30 条，按素材曝光均衡轮换，优先变化长画面；Hook 单独轮换。
- 默认与前 3 条比较 Body 素材复用，目标 50%。超标需明确接受，不因目标自动砍掉大部分数量。
- 预检锁定安排，修改配置后重检；产出通过媒体检查后列出。

复用率按共用源文件的画面秒数计算，不是视觉识别、平台查重或整批任意两条保证。

## 文档

| 文档 | 内容 |
| --- | --- |
| [新手教程](docs/USER_GUIDE.md) | 准备素材到成片，附实际例子 |
| [Codex 安装](INSTALL_FOR_CODEX.md) | 下载、校验、独立解压、启动 |
| [均衡轮换](docs/BALANCED_ROTATION.md) | 近期比较、曝光、限制 |
| [发布说明](docs/RELEASE_0.1.11.md) | 本次内容和验收边界 |
| [开发发布](docs/RELEASING.md) | 构建、测试、更新附件 |

## 已知限制

- 项目/任务状态在内存中，退出后需重新导入、选择目录和配音；已输出文件保留。
- Body 搜索最多每组 18 个素材、每组合 8 段、70000 个候选；不枚举同组文件所有排列。理论产能基于已枚举且勾选的候选。
- 每批最多安排 5000 条；0/留空也受此限制。算法不保证全局最优产能。
- 系统字幕字体随电脑变化；跨电脑一致时选择内置思源字体。
- 水印、“智能压测”、“普通/分组”等保留的历史占位项没有完整接入；本版不改变这些功能状态。
- 安装包未签名，Windows 可能显示未知发布者，请核对来源和校验值。

## 开发

`backend/` 后端、`desktop/` 桌面入口、`frontend/` 界面、`tests/` 测试、`scripts/` 构建与安装、`docs/` 教程、`licenses/` 许可。

Windows x64、Python 3.12 x64、Node.js 20：

```powershell
npm ci
npm run package
```

构建时下载并校验固定版本模型/工具/字体，运行时不下载模型。用户 Excel、配音、素材、成片、缓存、凭据和本机路径不入库。

参考 [VideoMatrix](https://github.com/w2968066/VideoMatrix--) 的布局与发布方式。应用源码 MIT；第三方资源保持各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本软件使用 HarmonyOS Sans Fonts，Copyright 2021 Huawei Device Co., Ltd.，未修改字体随软件分发，详见字体许可。

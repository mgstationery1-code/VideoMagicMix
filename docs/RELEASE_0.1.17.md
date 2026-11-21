## v0.1.17 · 配音对齐与人工复核

本次完整包来自已验收的 VoiceReview v0.1.17 Windows x64 目录。

### 下载哪个

- **VideoMagicMix-Setup-0.1.17-x64.exe**：推荐普通用户下载。双击并按向导安装，安装后打开 VideoMagicMix。
- **VideoMagicMix-Windows-0.1.17-x64.zip**：免安装完整包。解压到新文件夹后运行里面的 VideoMagicMix.exe，不能在压缩包里运行，也不能只复制 EXE。
- SHA256SUMS.txt：用于核对下载文件是否完整。
- latest.yml 和 blockmap：软件更新组件使用，普通用户不需要手动下载。

支持 Windows 10/11 x64。完整包包含本地后端、Whisper small 模型、FFmpeg/FFprobe、中文字体和拼音组件，无需 Codex、skill、Python、Node.js 或另外下载模型。准备好自己的 Excel、素材和配音即可开始使用。建议预留至少 10 GB 空间，并先生成 1–3 条检查音画字幕。

### 新功能

- 按完整文案顺序结合文字和拼音对齐，减少同音错字、部分口音造成的匹配失败。
- 疑难片段做有界二次识别，无法可靠确认时进入人工复核。
- 按 Body/分组显示 Excel 文案、实际识别和起止时间。
- 可试听对应原配音、调整时间、逐段点击“人工复核通过”。
- 更换配音或重新识别会清除旧确认；修改字幕样式保留确认。
- 保留 Hook 可选、多源素材、候选组合、均衡轮换、BGM、字幕位置预设等现有功能。

人工确认段落的小句按字数比例分配时间，不代表逐字声学对齐。匹配失败不能单凭错误提示认定配音读错；请先试听。

### 操作教程

- [新手教程](https://github.com/mgstationery1-code/VideoMagicMix/blob/main/docs/USER_GUIDE.md)
- [人工复核操作说明](https://github.com/mgstationery1-code/VideoMagicMix/blob/main/docs/VOICE_REVIEW.md)

### 验证范围

22 项新测试及 82 项回归测试通过。真实失败配音 6/6 Body 对齐成功，历史十 Body 配音通过；实际 EXE 的试听、逐段确认和渲染流程通过，生成的视频通过全片解码检查。本次发布另校验完整 ZIP 和安装器内程序文件与指定目录一致。

测试覆盖已知问题和主要流程，不代表任意录音、素材或电脑均已验证。安装包尚未代码签名，Windows 可能显示未知发布者；请核对官方仓库和校验值。

本机额外路径测试：相同安装包解出的程序在 C 盘用户目录中完成了启动、真实识别、试听和人工确认；在本机 D 盘临时目录出现 GPU 子进程加载失败。建议普通用户使用安装向导的默认路径。没有关闭 Electron 沙箱或修改原版运行组件。


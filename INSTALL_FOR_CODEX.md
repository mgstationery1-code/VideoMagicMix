# 在另一台电脑安装 VideoMagicMix

目标是 Windows 10/11 x64 上直接使用软件，不要求安装 skill 或从源码构建。

## 推荐流程

1. 查询 `mgstationery1-code/VideoMagicMix` 最新正式 Release，排除草稿/预发布。
2. 下载 `VideoMagicMix-Windows-版本-x64.zip` 和 `SHA256SUMS.txt`。
3. SHA256 校验通过后解压到新的独立目录，保留旧版，不混合文件。
4. 检查 EXE、app.asar、独立后端、Python DLL、模型、FFmpeg/FFprobe、字体和许可。
5. 启动 `VideoMagicMix.exe`，等待连接成功，向用户报告绝对路径和教程。

仓库提供自动执行下载、校验、独立解压和启动的脚本（PowerShell 5.1+）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-windows.ps1
```

可选 `-Version 0.1.11` 固定版本、`-Destination D:\VideoMagicMix-0.1.11` 指定不存在的目录、`-NoLaunch` 只安装。默认放在 `%LOCALAPPDATA%\Programs\VideoMagicMix-版本`。已有目录会停止，绝不删除或覆盖旧版。脚本创建桌面快捷方式。

也可下载同一 Release 的 Setup EXE，使用安装向导。不默认进行系统级静默安装。

## 注意

- 不把 GitHub 自动生成的 Source code ZIP 当作运行包，不单独复制 EXE。
- 不用 pip/npm 安装运行依赖；完整包已含模型及运行库。
- 公开 Release 下载不需要向用户索取 token。
- 不上传用户 Excel、录音、视频、日志或凭据。
- 启动成功不等于用户素材已验收；请先生成 1–3 条检查音画字幕。
- 网络阻断时报告地址和具体错误，不改用不明来源。

交付版本、EXE/快捷方式位置和 [新手教程](docs/USER_GUIDE.md)。项目状态不持久化，重启后需重新导入；CPU 速度随电脑性能变化。

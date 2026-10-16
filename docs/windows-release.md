# Windows 运行与打包说明

声息支持在 Windows 上从源码运行和打包为桌面应用。本文记录差异点与注意事项。

## 从源码运行（Windows）

```powershell
git clone https://github.com/donghyq/ListenWise.git
cd lawflow
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

在浏览器打开 <http://127.0.0.1:8080>。

要点：

- **Python**：3.10+。`requirements.txt` 中 macOS 专用依赖（pyobjc）会自动跳过。
- **数据目录**：源码运行时数据保存在仓库下的 `data/`；打包后的桌面版保存在 `%LOCALAPPDATA%\LawFlow\data`（可用环境变量 `LAWFLOW_DATA_DIR` 覆盖）。
- **FFmpeg**：完整 MP3 合成与 M4B 导出需要 FFmpeg（含 `ffprobe`）并加入 PATH。安装方式任选：
  ```powershell
  winget install Gyan.FFmpeg
  # 或 scoop install ffmpeg
  ```
  仅导入、整理和审阅文字不需要 FFmpeg。

## 语音合成（TTS）

| 方式 | 说明 |
| --- | --- |
| Windows 本地语音（推荐） | 设置 → 语音合成 → 「Windows 本地语音」。使用系统 SAPI 语音（如 Microsoft Huihui Desktop），完全离线、免费、无需密钥；生成 MP3 需 FFmpeg。 |
| Edge TTS | 免 Key 在线语音，音质更自然；失败时自动回落到本机语音。属实验性在线能力。 |
| 兼容 /audio/speech、MiniMax | 云端 TTS，需要地址与密钥。 |

- 「检测本机音色」按钮在 Windows 上会列出 SAPI 语音（端点 `/api/settings/local-voices`，按平台自动分流）。
- 未配置任何云端 TTS 时，合成会自动回落本机语音（Mac 用 `say`，Windows 用 SAPI），保证「原文朗读」开箱即用。

## 打包桌面应用

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_app.ps1
```

脚本会用 PyInstaller 打出 `dist\Shengxi\`，并压缩为 `dist\Shengxi-<版本>-win64.zip`。产物依赖：

- **WebView2 Runtime**：pywebview 在 Windows 上基于 Edge WebView2，Windows 10/11 一般已内置；缺失时到 Microsoft 官网安装。
- **FFmpeg**：不由打包脚本携带，最终用户机器需自行安装（与 macOS DMG 的做法一致）。

桌面版行为与 macOS 一致：监听局域网以支持手机扫码收听（`/m/` 一次性链接），其余接口仅限本机；数据保存在 `%LOCALAPPDATA%\LawFlow\data`；「导出到哪了」用资源管理器定位文件（`explorer /select`）。

## 与 macOS 的差异清单

| 项目 | macOS | Windows |
| --- | --- | --- |
| 数据目录（打包版） | `~/Library/Application Support/LawFlow/data` | `%LOCALAPPDATA%\LawFlow\data` |
| 本地 TTS | `say` 命令 | PowerShell + System.Speech（SAPI） |
| 文件定位 | Finder（`open -R`） | 资源管理器（`explorer /select`） |
| 打包产物 | .app / .dmg | 文件夹 + zip |

## 已知限制

- Windows 环境变量单值上限 32767 字符，本机若存在超长环境变量，`tests/test_improvements.py` 中清空环境变量的用例可能报错（与功能无关）。
- SAPI 语音质量依系统语音包而定；建议在「设置 → 时间和语言 → 语音」安装中文语音包。

# LawFlow 桌面版发布

LawFlow 面向客户时采用“本地桌面 App + GitHub Releases”模式，不运行公共业务后端。客户材料、项目数据库、讲稿与音频保存在客户电脑；模型和语音服务使用客户自行配置的凭据。

桌面版默认使用独立原生窗口打开本地工作台；仅在本机窗口组件初始化失败时回退到默认浏览器。业务服务仍只监听 `127.0.0.1`，不会暴露到局域网。

## 下载与仓库

源码与正式安装包使用同一个公开仓库：`donghyq/ListenWise`。通过该仓库的 Releases 页面下载安装包；只有签名、公证和验证均通过的构建才发布为正式版本。

应用默认从该仓库检查更新；开发部署可以用 `LAWFLOW_RELEASE_REPOSITORY=owner/repo` 覆盖。不要将客户材料、构建日志或签名凭据提交到仓库或发布附件。

## 首次配置

配置 Actions secrets：

- `APPLE_CERTIFICATE_P12_BASE64`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_SIGNING_IDENTITY`：例如 `Developer ID Application: Example (TEAMID)`
- `APPLE_API_KEY_P8_BASE64`
- `APPLE_API_KEY_ID`
- `APPLE_API_ISSUER_ID`
- `KEYCHAIN_PASSWORD`：仅用于 Actions 临时钥匙串

不要将以上内容提交到仓库或写入 App。Actions 使用仓库自带的 `GITHUB_TOKEN` 发布 Release。

## 发布步骤

1. 修改 `app/version.py`，例如从 `0.1.0` 更新为 `0.2.0`。
2. 更新面向客户的版本说明。
3. 合并并确认主分支测试通过。
4. 创建并推送完全一致的标签：`v0.2.0`。
5. GitHub Actions 在 `macos-15` 和 `macos-15-intel` 分别构建 arm64、x86_64 安装包。
6. 工作流执行 Developer ID 签名、Apple 公证、staple 和 Gatekeeper 校验。
7. 两个 DMG 与 SHA256 自动发布到 `donghyq/ListenWise` 的 Releases 页面。

标签版本与 `app/version.py` 不一致，或缺少证书、公证密钥时，工作流会失败，不会发布未签名客户包。

## 本地开发构建

安装 PyInstaller 后运行：

```bash
python3 -m pip install -r requirements.txt pyinstaller
bash scripts/build_macos_app.sh
```

未配置 `LAWFLOW_SIGNING_IDENTITY` 时只生成临时签名包，供开发验证，不能作为客户下载版本。正式构建使用：

```bash
LAWFLOW_VERSION=0.1.0 \
LAWFLOW_REQUIRE_SIGNING=1 \
LAWFLOW_SIGNING_IDENTITY="Developer ID Application: Example (TEAMID)" \
bash scripts/build_macos_app.sh
```

## 发布验收

- 在没有 Python、Homebrew 和源码目录的干净 Mac 上安装并启动。
- 分别验证 Apple Silicon 和 Intel 包。
- 运行 `spctl`、`codesign` 和 `stapler validate`。
- 新建项目、导入公开测试材料、重启 App，确认数据仍在。
- 从旧版升级，确认数据库与项目目录未被覆盖；发生 schema 升级时，`data/backups/` 应生成迁移前 SQLite 备份，最多保留三份。
- 生成试听与完整音频；当前安装包仍需解决 FFmpeg 内置或原生替代后才能宣称音频零依赖。
- 发布一个更高的测试版本，确认首页出现更新提示并打开正确的公共 Release 页面。

## 无服务器能力边界

- 每日速听只在 App 运行时调度；应补充启动时追赶错过任务。
- ChatGPT Plus 复制/粘贴协作和本机 Agent Skill 可用。
- ChatGPT 云端不能直接访问客户电脑的 `127.0.0.1`；远程 MCP 不属于默认客户版能力。
- Docker 仅用于本机开发，端口只映射到 `127.0.0.1`。

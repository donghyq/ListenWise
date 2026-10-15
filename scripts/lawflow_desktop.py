"""Desktop launcher for the local LawFlow web application (macOS / Windows)."""
from __future__ import annotations

import signal
import socket
import sqlite3
import threading
import time
import webbrowser
import os
import shutil
import sys
from pathlib import Path

import uvicorn


LOADING_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;display:grid;place-items:center;height:100vh;background:#f7f5ef;font-family:-apple-system,"Noto Sans SC",sans-serif;color:#14251f}
.card{text-align:center}.mark{width:58px;height:58px;margin:0 auto 18px;display:grid;place-items:center;border-radius:15px;background:#1d5b49;color:#fff;font:700 27px/1 "Noto Serif SC",Georgia,serif}
h1{margin:0 0 8px;font-size:20px;letter-spacing:-.02em}p{margin:0;color:#41534d;font-size:13px}
.bar{width:190px;height:3px;margin:24px auto 0;border-radius:99px;background:#e6e1d5;overflow:hidden}
.bar::after{content:"";display:block;width:40%;height:100%;border-radius:99px;background:#1d5b49;animation:slide 1.1s ease-in-out infinite}
@keyframes slide{0%{transform:translateX(-110%)}100%{transform:translateX(290%)}}
</style></head><body><div class="card"><div class="mark">声</div><h1>声息</h1><p>正在启动本地服务，请稍候…</p><div class="bar"></div></div></body></html>"""


def error_page(message: str) -> str:
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return LOADING_PAGE.replace(
        "正在启动本地服务，请稍候…",
        f"启动失败：{safe}<br/>请关闭窗口后重新打开声息。",
    ).replace(".bar{", ".bar-hidden{")

def project_count(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return 0


def migrate_legacy_data(data_dir: Path, legacy_dir: Path) -> bool:
    """首次启动桌面版时，把开发版数据安全复制到 Application Support。"""
    if data_dir.resolve() == legacy_dir.resolve():
        return False
    if project_count(data_dir / "app.db") or not project_count(legacy_dir / "app.db"):
        return False
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    if data_dir.exists() and any(data_dir.iterdir()):
        backup = data_dir.parent / "migration-backups" / ("before-legacy-import-" + time.strftime("%Y%m%d-%H%M%S"))
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(data_dir, backup)
    shutil.copytree(legacy_dir, data_dir, dirs_exist_ok=True)
    return True


def choose_port() -> int:
    requested = os.getenv("LAWFLOW_PORT")
    candidates = [int(requested)] if requested else list(range(8080, 8090))
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("声息无法找到可用的本地端口，请关闭占用 8080–8089 的程序后重试。")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if sys.platform == "win32":
        default_data = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "LawFlow" / "data"
        default_legacy = Path.home() / "Projects" / "lawflow" / "data"
    else:
        default_data = Path.home() / "Library/Application Support/LawFlow/data"
        default_legacy = Path.home() / "Projects/lawflow/data"
    data_dir = Path(os.getenv("LAWFLOW_DATA_DIR", str(default_data))).expanduser()
    legacy_dir = Path(os.getenv("LAWFLOW_LEGACY_DATA_DIR", str(default_legacy))).expanduser()
    migrate_legacy_data(data_dir, legacy_dir)
    os.environ["LAWFLOW_DATA_DIR"] = str(data_dir)

    port = choose_port()
    # 始终监听局域网，免去「开启手机访问要重启」的麻烦；安全由 main.py 中间件保证：
    # 局域网客户端只能访问 /m/* 收听页（需一次性 token），其余接口一律拒绝。
    bind_host = "0.0.0.0"
    url = f"http://127.0.0.1:{port}"
    holder: dict[str, uvicorn.Server] = {}
    try:
        import webview
    except Exception:
        webview = None

    if webview is not None:
        # 用原生 html= 渲染加载页。不要用 data: URI：pywebview 会把它当本地文件
        # 交给内部 HTTP 服务器解析，产生一次短暂约 404 错误页。
        window = webview.create_window(
            "声息",
            html=LOADING_PAGE,
            width=1440,
            height=960,
            min_size=(1120, 760),
            background_color="#f7f5ef",
        )

        def boot() -> None:
            try:
                from app.main import app  # 重导入放在窗口出现之后，避免长时间白屏

                config = uvicorn.Config(app, host=bind_host, port=port, log_level="warning")
                server = uvicorn.Server(config)
                holder["server"] = server
                worker = threading.Thread(target=server.run, daemon=True)
                worker.start()
                for _ in range(600):
                    if server.started:
                        break
                    time.sleep(0.1)
                if server.started:
                    window.load_url(url)
                else:
                    window.load_html(error_page("本地服务未能在 60 秒内就绪"))
            except Exception as error:  # noqa: BLE001 - 启动失败也要把原因呈现给用户
                try:
                    window.load_html(error_page(str(error)))
                except Exception:
                    pass

        webview.start(boot)
        server = holder.get("server")
        if server is not None:
            server.should_exit = True
        return

    from app.main import app

    config = uvicorn.Config(app, host=bind_host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)

    webbrowser.open(url)

    def stop(*_args):
        server.should_exit = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.join()


if __name__ == "__main__":
    main()

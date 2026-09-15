"""test_push_bootstrap_teardown —— 升级前必须**释放监听端口**（2026-09-15 Colab 事故）。

现象（用户实测）：Colab push-first 走到
  `code.zip 已解包 -> /tmp/worker-code（升级完整 worker_server）`
  → `worker_server 30s 未就绪` → `SystemExit: -1`，cell 挂、隧道下线，
  控制台侧只看到「推送成功但状态查询 530」（连续 15 次 HTTP 530 退避）。
`!cat /tmp/remote-worker-serve/serve.log` 给出真因：
  `[worker-serve] ERROR: 端口 127.0.0.1:8790 已被占用——拒绝启动（禁止双监听）`

根因：`run_push_first` 的 finally 只调了 `srv.shutdown()`。`socketserver` 语义里
`shutdown()` 只停 `serve_forever()` 循环，**不关监听套接字**（那是 `server_close()`
的职责）。而 push-first 让引导服务与升级后的完整 worker_server **共用同一个
`push_port`**，所以父进程不放手 ⇒ 子进程必然 bind 失败 ⇒ 30s 超时 ⇒ 整条链路死。

判据（平台无关）：`shutdown()` 之后 `srv.socket` 仍是套接字对象；`server_close()`
之后被置为 `None`。只调 `shutdown()` 时本文件的第一个断言会红。
"""

from __future__ import annotations

import re
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.push_bootstrap import (
    close_bootstrap_server,
    start_bootstrap_server,
    tail_text_lines,
)


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _running_bootstrap(port: int, tmp_path: Path):
    srv = start_bootstrap_server(port, "tok", tmp_path, lambda raw: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_teardown_closes_listening_socket(tmp_path: Path) -> None:
    """`close_bootstrap_server` 必须真正关掉监听套接字（只 shutdown() 红）。

    注意判据不是 `srv.socket is None`：CPython 的 `TCPServer.server_close()` 只是
    `self.socket.close()`，**不会**把 `self.socket` 置 None（对象仍在，但 `fileno()`
    变 -1）。所以判「已关闭」要看 `fileno()`。只调 `shutdown()` 时套接字仍开着
    （`fileno() >= 0`）⇒ 端口没释放 ⇒ 本断言红。
    """
    srv = _running_bootstrap(_free_port(), tmp_path)

    close_bootstrap_server(srv)

    sock = srv.socket
    assert sock is None or sock.fileno() == -1, "监听套接字未关闭 ⇒ 端口没释放"


def test_port_free_for_next_bind_after_teardown(tmp_path: Path) -> None:
    """释放后同一端口可被**普通** socket 重新 bind —— 这正是随后 worker_server 要做的事。

    新 socket 刻意**不设** SO_REUSEADDR：Windows 上「双 SO_REUSEADDR」才允许抢占，
    不设即能在两个平台上都正确判出「端口还被占着」。
    """
    port = _free_port()
    srv = _running_bootstrap(port, tmp_path)

    close_bootstrap_server(srv)

    again = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        again.bind(("0.0.0.0", port))  # 未释放则抛 OSError: address already in use
    finally:
        again.close()


def test_call_site_uses_teardown_helper() -> None:
    """调用点必须走 helper —— 源码守卫（上面两个测试只钉住 helper 自身的行为）。

    为什么这里只能用源码断言：`run_push_first` 需要 `ensure_cloudflared()`（会去下载
    cloudflared）、然后阻塞等首个 job、再 spawn 子进程，无法在单测里驱动。而本次事故的
    回归形态恰恰是**调用点**被写回裸 `srv.shutdown()`（helper 被绕过）。所以补这条守卫：
    模块内 `srv.shutdown()` 只允许出现在 helper 内部一次，且 finally 必须调 helper。
    """
    src = (ROOT / "remote" / "push_bootstrap.py").read_text(encoding="utf-8")
    flat = " ".join(src.split())

    # finally 块必须调 helper（它内部才做 server_close 释放端口）。容忍中间的注释行。
    assert re.search(
        r"finally:\s*(?:#[^\n]*\n\s*)*close_bootstrap_server\(srv\)", src
    ), "run_push_first 的 finally 必须调用 close_bootstrap_server(srv) 释放监听端口"
    # 裸 shutdown() 只允许 helper 内部那一处；调用点再写一次就会变成 2
    assert flat.count("srv.shutdown()") == 1, (
        "srv.shutdown() 应只在 close_bootstrap_server 内出现一次——"
        "调用点若绕过 helper，端口不会被释放（2026-09-15 Colab 事故的回归形态）"
    )
    assert flat.count("srv.server_close()") == 1


# ─────────── 30s 就绪失败时把 serve.log 尾部摊进 cell（2026-09-15） ───────────


def test_tail_text_lines_returns_last_n(tmp_path: Path) -> None:
    """只取末 n 行，并显式标注略去了多少行（免得看着像完整日志）。"""
    f = tmp_path / "serve.log"
    f.write_text("\n".join(f"line{i}" for i in range(1, 101)), encoding="utf-8")

    out = tail_text_lines(f, 5)

    assert out[0].startswith("...")
    assert out[1:] == ["line96", "line97", "line98", "line99", "line100"]


def test_tail_text_lines_short_file_has_no_marker(tmp_path: Path) -> None:
    """行数不足 n 时原样返回，不加「略去」标记。"""
    f = tmp_path / "serve.log"
    f.write_text("a\nb\n", encoding="utf-8")

    assert tail_text_lines(f, 40) == ["a", "b"]


def test_tail_text_lines_missing_file_is_not_fatal(tmp_path: Path) -> None:
    """文件不存在也不能抛——失败路径上再抛异常会盖掉真正的错误信息。"""
    out = tail_text_lines(tmp_path / "nope.log", 5)

    assert len(out) == 1
    assert "无法读取" in out[0]

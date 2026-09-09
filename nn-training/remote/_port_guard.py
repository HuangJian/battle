"""remote/_port_guard.py — 启动前端口占用守卫（不允许双监听，2026-09-09 事故）。

Python http.server 的 ThreadingHTTPServer 继承 HTTPServer.allow_reuse_address=1，
绑定时设置 SO_REUSEADDR。Linux 上第二个 bind 会 EADDRINUSE 崩溃；但 **Windows 的
SO_REUSEADDR 允许两个 socket 同时 bind 同一端口**——后启动的进程不崩，而是静默
变成永远收不到连接的僵尸（hub_server 8787 曾同时 LISTENING 两个实例，实测新连接
全部进先绑定者）。本守卫在 bind 之前做 TCP 连接探测：端口已有活监听者 → 抛错
拒绝启动（后启动者响亮失败，而非静默共存）。TIME_WAIT 残留不误伤：只剩 TIME_WAIT
时连接被拒 → 判定端口可绑（SO_REUSEADDR 正常绑定，不产生重启抖动）。
"""

from __future__ import annotations

import socket


def ensure_port_free(host: str, port: int, *, timeout: float = 0.3) -> None:
    """端口上已有活监听者则抛 RuntimeError（bind 前调用）。

    探测成功（三次握手完成）= 有人在监听 → 拒绝；连接被拒/超时 = 端口可绑。
    竞态窗口（探测后、bind 前他人抢占）极小，对本地开发工具可接受。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            raise RuntimeError(
                f"端口 {host}:{port} 已被占用——拒绝启动（禁止双监听；请先停止占用者再启动）"
            )
    except OSError:
        return

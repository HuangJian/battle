"""e2e/test_deliver_backup_contract_e2e.py —— 补传腿对**网络盘**与**训练线程**的三条契约。

用户口径（2026-10-29，第 N 次重复）：
  「**只在回传失败时，才需要写入网络盘**」 · 「**传输进程不要阻塞训练进程**」

为什么必须是 e2e：这条腿的每一步单测都是绿的，坏在**顺序与时机**上——
真 hub（本机临时端口）+ 真 `OfflineDeliverer` + 真产物目录 + 真抽屉目录，才看得见
「一轮明明 60 秒后就能送到，却先在网络盘上写 1.5MB 再删掉」这种事故（现场 `art` 从
1.1s 抖到 88s 就是它）。

守三件事：
  ① **送达的轮次一个字节都不写网络盘**（含：刚写下、还在飞的轮次也不许先备一份）；
  ② 只有**卡住超过宽限期**（`BACKUP_GRACE_SEC`）的轮次才落抽屉；
  ③ hub 挂死时 `submit_round` **立刻返回**（训练线程不等网络）。

纪律：不 spawn bun/node、不加载 torch；HTTP 全在本机临时端口；单用例 < 5s。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from remote.artifacts import ArtifactStore
from remote.deliver_proc import DelivererProcess
from remote.offline_deliver import BACKUP_GRACE_SEC, OfflineDeliverer


class _ProbeProcess(DelivererProcess):
    """父侧投递器，但**不起子进程**（`_append` 被替换成记录）：只验训练线程那一段。"""

    def _append(self, obj: dict) -> None:
        self.sent = dict(obj)


class _Hub:
    """本机假 hub：`/ping` 探活 + `/offline/artifact` 收产物（可按模式失败/挂住）。"""

    def __init__(self, *, mode: str = "ok", hang_sec: float = 0.0) -> None:
        self.mode = mode
        self.hang_sec = hang_sec
        self.posts: list[int] = []
        hub = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # 静音
                return

            def _reply(self, code: int, body: dict | None = None) -> None:
                raw = json.dumps(body or {}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if hub.hang_sec:
                    # sleep-ok: 夹具模拟慢端点（挂住的一端），不是「等对方先跑」
                    time.sleep(hub.hang_sec)
                if self.path.startswith("/ping"):
                    self._reply(200, {"ok": True})
                    return
                self._reply(404, {"error": "not found"})

            def do_POST(self):
                if hub.hang_sec:
                    # sleep-ok: 夹具模拟慢端点（挂住的一端），不是「等对方先跑」
                    time.sleep(hub.hang_sec)
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if not self.path.startswith("/offline/artifact"):
                    self._reply(404, {"error": "not found"})
                    return
                hub.posts.append(1)
                if hub.mode == "fail":
                    self._reply(500, {"error": "boom"})
                    return
                self._reply(200, {"ok": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _artifacts(tmp_path: Path) -> Path:
    """一段真产物目录：**一轮**训练（账本行 + 权重 + opt），未投递。"""
    root = tmp_path / "artifacts"
    root.mkdir()
    ArtifactStore(root, run_id="run-1", log=lambda _m: None).checkpoint(
        1, weights_json=b'{"format":"battle-weights"}', opt_tar=b"opt-bytes", row={"it": 1}
    )
    return root


def _drawer_bytes(backup_root: Path) -> int:
    if not backup_root.exists():
        return 0
    return sum(p.stat().st_size for p in backup_root.rglob("*") if p.is_file())


def _deliverer(root: Path, backup_root: Path, hub: _Hub, **kw) -> OfflineDeliverer:
    return OfflineDeliverer(
        base_url=hub.url,
        token="tok",
        run_id="run-1",
        artifacts_dir=root,
        backup_root=backup_root,
        timeout=2.0,
        log=lambda _m: None,
        **kw,
    )


def test_delivered_round_never_writes_the_network_drawer(tmp_path: Path) -> None:
    """① 送达 ⇒ 网络盘**零写入**（不是「写完再删」）。

    这一条是「每轮先在网络盘写 1.5MB、60 秒后投递成功又删掉」的事故判据：现场抽屉日志
    每轮都是 `备份=1 轮待投（本轮新写 1）`，而那一轮当时**正要**被送出去。
    """
    hub = _Hub(mode="ok")
    drawer = tmp_path / "network" / "backup"
    try:
        d = _deliverer(_artifacts(tmp_path), drawer, hub)
        assert d.sync() == 1, "这一轮应当送达"
        assert hub.posts, "应当真的 POST 过"
    finally:
        hub.close()
    assert _drawer_bytes(drawer) == 0, f"送达的轮次不许碰网络盘，抽屉里有 {sorted(drawer.rglob('*'))}"


def test_failed_round_is_backed_up_only_after_the_grace(tmp_path: Path) -> None:
    """② 失败才备份，而且要**卡住超过宽限期**才备份（一次探活没应答不算失败）。"""
    hub = _Hub(mode="fail")
    root = _artifacts(tmp_path)
    drawer = tmp_path / "network" / "backup"
    try:
        d = _deliverer(root, drawer, hub)
        assert d.sync() == 0, "hub 500 ⇒ 没送达"
        assert hub.posts, "应当真的尝试过"
        assert _drawer_bytes(drawer) == 0, (
            "刚失败一次、宽限期内不许写网络盘（每轮 1.5MB 的写+删就这么来的）"
        )
        # 模拟「这一轮卡了 10 分钟还没送出去」——判据是**产物的年龄**，不是内存状态
        # （父侧补传器每拍新建一个只读镜像，内存态在它眼里永远是空的）。
        old = time.time() - (BACKUP_GRACE_SEC + 60.0)
        os.utime(root / "it-001" / "weights.json", (old, old))
        d.sync()
    finally:
        hub.close()
    assert _drawer_bytes(drawer) > 0, "卡过宽限期的轮次必须落一份到网络盘"
    assert (drawer / "it-001").is_dir(), f"落下来的应当就是那一轮：{sorted(drawer.rglob('*'))}"


def test_submit_round_does_not_block_on_a_hanging_hub(tmp_path: Path) -> None:
    """③ hub 挂死（`/ping` 挂 3s）时 `submit_round` 必须**立刻**返回：训练线程不等网络。

    现场：传输走的是**独立进程**，但父侧 `submit_round` 里曾同步做「观测账本 + 备份」——
    那一次 1.5MB 的网络盘写入就压在训练线程上（`art` 从 1.1s 抖到 88s）。
    """
    hub = _Hub(mode="ok", hang_sec=3.0)
    d = _deliverer(_artifacts(tmp_path), tmp_path / "network" / "backup", hub, background=True)
    try:
        t0 = time.monotonic()
        d.submit_round(1)  # 挂死的 hub 也不能让这一句变慢
        dt = time.monotonic() - t0
        # timing-ok: 契约上界 ——「非阻塞」的判据就是它不等**挂住 3s** 的端点（相对那个夹具）
        assert dt < 0.3, f"submit_round 被网络拖住了：{dt:.2f}s"
    finally:
        d.close(timeout=0.1)
        hub.close()


@pytest.mark.parametrize("force", [False, True])
def test_force_backs_up_anything_pending(tmp_path: Path, force: bool) -> None:
    """段末（`force=True`）必须把**所有**还欠着的轮次都备下来——那是最后一次机会。"""
    hub = _Hub(mode="fail")
    drawer = tmp_path / "network" / "backup"
    try:
        d = _deliverer(_artifacts(tmp_path), drawer, hub)
        d.sync()
        d.backup_pending(force=force)
    finally:
        hub.close()
    if force:
        assert _drawer_bytes(drawer) > 0, "段末 sweep 必须落盘"
    else:
        assert _drawer_bytes(drawer) == 0, "非段末：宽限期内不许写"


def test_process_mode_submit_round_does_not_touch_the_drawer(tmp_path: Path) -> None:
    """进程模式的**父侧**（训练线程）：`submit_round` 不许碰网络盘。

    旧实现里它每轮先 `backup_pending()` —— 那是训练线程上的 1.5MB 网络盘写入，写完 60 秒
    投递成功再删掉。这里用「年龄已过宽限」的轮次当诱饵：只要 `submit_round` 里还有那句，
    断言就会红。
    """
    root = _artifacts(tmp_path)
    drawer = tmp_path / "network" / "backup"
    old = time.time() - (BACKUP_GRACE_SEC + 60.0)
    os.utime(root / "it-001" / "weights.json", (old, old))
    dp = _ProbeProcess(
        base_url="http://127.0.0.1:9",  # 连不上：本用例只看「有没有碰抽屉」
        token="tok",
        run_id="run-1",
        artifacts_dir=root,
        backup_root=drawer,
        log=lambda _m: None,
    )
    dp.submit_round(1)
    assert _drawer_bytes(drawer) == 0, "父侧 submit_round 不许写网络盘（它跑在训练线程上）"
    # 反证：同样的轮次，显式备份（旧实现就在 submit_round 里调它）确实会写盘 ⇒ 诱饵是有效的
    dp.backup_pending()
    assert _drawer_bytes(drawer) > 0, "诱饵无效：这一轮根本不会被备份，用例就抓不到回归"

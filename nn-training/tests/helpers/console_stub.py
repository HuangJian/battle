"""tests/helpers/console_stub.py — 控制台（dashboard）的**确定性假替身**（2026-10-25）。

## 为什么需要它

`hub/task_pack.py` 的控制台腿（`autoOfflineHandoff` / `exportTaskBundle` / `export-status`）是
**同步**走的：`GET /offline/claim` 成功分支会请控制台核对一次任务包新鲜度。测试里把它指到
「没人听的死端口」（`tests/conftest.py::TEST_CONSOLE_URL` = `http://127.0.0.1:9`）时：

* 语义上没问题（不可达 ⇒ 响亮降级成「请手动导出」），
* 但**墙钟**取决于主机的 TCP 行为：本机（Windows，2026-10-25 实测）`ECONNREFUSED` 要 **2.06s**
  才回来（三次连测 2.071 / 2.042 / 2.053），某些沙箱把回环 SYN 丢得更久（旧注记记过 8s）。

于是每条走过 claim 的 e2e 都白付 N × 2s。`ep · e2e/test_hold_e2e.py` 两条用例共 4 次 claim =
8.2s，占它们墙钟的 45%。

## 形状

`NoHandoffConsole`：起一个真的 `ThreadingHTTPServer`（127.0.0.1:0，端口归本进程），
**一切路径回 404**。这正是「控制台不存在 / 太旧」的确定性降级分支：`trigger_task_bundle_export`
走 `http 404` 分支、`auto_handoff_decision` 记 failed、claim 回执里写「控制台不可达/不接受」——
与死端口**同语义**，只是零等待、与主机 TCP 行为无关。

这个类原先只活在 `e2e/test_auto_handoff_e2e.py`（2026-10-20 为
`test_auto_loop_runs_the_claimable_batch_in_order` 恒红而写）；2026-10-25 提到这里，给
hold / offline_training / multi_course 三条 e2e 共用（同一份语义只能有一处实现）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class NoHandoffConsole:
    """假控制台：**没有**自动交接/导出端点（一律 404）——「控制台不在 / 太旧」的替身。

    用法（e2e 起 hub 子进程时）：

        console = NoHandoffConsole()
        try:
            env = {**os.environ, "BCITY_CONSOLE_URL": console.url, ...}
            ...
        finally:
            console.close()
    """

    def __init__(self, *, body: str = "not found") -> None:
        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # BaseHTTPRequestHandler 的约定方法名
                self._deny()

            def do_GET(self) -> None:
                self._deny()

            def _deny(self) -> None:
                out = json.dumps({"error": body}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args: object) -> None:  # 静音：测试不想被 access log 刷屏
                return None

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def __enter__(self) -> NoHandoffConsole:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

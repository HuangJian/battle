"""remote/worker_proc.py —— worker 的**进程生命周期**：监督 / 热替换 / 云机停机（2026-09-24 从 `remote/worker.py` 下沉）。

worker 跑在**子进程**里，所以「换代码」这件事只能靠**退出码 + 监督器重拉**完成：`os.execve`
会原地替换 notebook 的 kernel 镜像，输出流随之断掉、Jupyter 判定 kernel 死亡（2026-09-11 线上
事故的完整链条，见 `_request_reload` 的 docstring）。整条链的四件事都在本模块：

  1. `HOT_RELOAD_EXIT`（**86**）：子进程请求重启的**唯一信号**。不用 0 —— 「处理失败」与
     「正常完成」必须可区分，退出码是判据；
  2. `_request_reload`（**子进程侧**）：有监督器 ⇒ 以该码干净退出交人重拉；没有（裸
     `worker_loop` 直调）⇒ 返回 False，调用方降级为提示人工重启；
  3. `supervise_worker`（**父进程侧**）：子进程 stdout/stderr 逐行转发；收到 86 就**用同一套
     参数**重新拉起（fresh 进程 ⇒ `sys.modules` 必然为空 ⇒ 新代码一定生效），输出流不中断；
  4. `_release_cloud_machine`：停机达令的落地（写 `/tmp/battle-halt-request` 哨兵 / Colab
     `unassign`）——它不是「退出 worker」，而是「真把机器停掉」（§386：worker 退出 ≠ 停机省钱）。

**零 `remote.*` 依赖**（只用 stdlib）⇒ 账本里秩为 **L0**：任何模块都能在它下面踩，不可能成环。

`remote/worker.py` 只做 `from remote.worker_proc import … as …` 的显式转发，所以
`W.HOT_RELOAD_EXIT` / `W._request_reload` / `W.supervise_worker` / `W._release_cloud_machine`
这些**既有注入点照旧有效**：宿主 `main()` 与 `worker_loop()` 读的都是**自己命名空间**里的名字
（`setattr(W, "supervise_worker", …)` / `setattr(W, "_release_cloud_machine", …)` 实测如此）。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: 热替换退出码：worker 子进程代码变更时以该码退出，**监督器**（supervise_worker /
#: 新版 main()）收到后用同一套参数重新拉起子进程（fresh 进程 → sys.modules 必然为空
#: → 新代码生效）。不用 0（=正常完成）：处理失败/退出原因必须可区分。
HOT_RELOAD_EXIT = 86


def _request_reload(restart_argv: list[str] | None, log=lambda msg: None) -> bool:
    """热替换：有监督器 → 以 HOT_RELOAD_EXIT 干净退出，交监督器拉起新进程；无 → False。

    为什么不再 os.execve（2026-09-11 线上事故）：notebook 里 worker_loop 跑在 kernel
    进程内，execv 会**原地替换 kernel 镜像**——ipykernel 对 sys.stdout 的重定向对象
    随之丢失（单元格输出直接断流，只剩 kernel server 的控制台能看见），且 ZMQ 执行
    服务不再应答，Jupyter 判定 kernel 死。用户看到"自重启"后单元格没下文 → 按停止 →
    SIGINT 打断正在跑的 worker → kernel 重启 → 云端会话报废（本次事故的完整链条）。

    现统一契约：worker 以退出码 HOT_RELOAD_EXIT 退出，由 **监督器**（supervise_worker）
    用同一套参数重新拉起子进程——fresh 进程里 sys.modules 必然为空，新代码一定生效；
    监督器本身（notebook 的 kernel）不 execv、输出流不断、也不被判定死亡。

    restart_argv 仍只认**显式传入**：notebook 里 sys.argv 是 kernel 自己的参数。
    None = 没有监督器（裸 worker_loop 直调）→ 返回 False，调用方降级为提示人工重启。
    """
    if not restart_argv:
        log("自重启不可用：未提供 restart_argv（无监督器可拉起新进程）")
        return False
    log(f"代码已变更 —— 以退出码 {HOT_RELOAD_EXIT} 交监督器重启（fresh 进程加载新代码）")
    raise SystemExit(HOT_RELOAD_EXIT)


def supervise_worker(
    restart_argv: list[str],
    *,
    cmd: list[str] | None = None,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker-supervisor] {msg}", flush=True),
) -> int:
    """监督器：worker 跑在**子进程**里，热替换以 exit(HOT_RELOAD_EXIT) 请求重启。

    - 输出转发：子进程 stdout/stderr → 本进程 stdout 逐行转发。notebook 里本函数在
      kernel 进程内执行，转发让日志持续进单元格；CLI 下等价于直通。
    - 热替换：子进程退 HOT_RELOAD_EXIT → 用同一套参数重新拉起（fresh 进程加载新代码）。
      换代码从"打掉 kernel"变成一次无害的拉起重演，kernel/输出流永不中断。
    - KeyboardInterrupt：先终止子进程再上抛（中断单元格不会留下孤儿 worker）。
    - 返回子进程最终退出码（热替换已内部消化，不会带 86 返回）。

    cmd：测试注入口（默认 [sys.executable, -u, -m, remote.worker, *restart_argv]）。
    """
    nn_root = str(Path(__file__).resolve().parents[1])  # remote/ -> nn-training/
    if cmd is None:
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "remote.worker",
            *(str(a) for a in restart_argv),
        ]
    while True:
        env = dict(os.environ)
        # 子进程要能 import remote.worker（notebook 的 sys.path 不进子进程，只能靠 PYTHONPATH）
        env["PYTHONPATH"] = nn_root + os.pathsep + env.get("PYTHONPATH", "")
        # 子进程 main() 看到该标记直跑 worker_loop，不再递归监督
        env["REMOTE_WORKER_CHILD"] = "1"
        log(f"spawn worker 子进程（{len(restart_argv)} 参数）")
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            child_out = proc.stdout
            if child_out is None:  # stdout=PIPE，结构化保证非空；只为让我 mypy 类型收窄
                raise RuntimeError("supervise_worker: stdout=PIPE 却拿不到管道（不该发生）")
            for line in child_out:  # `-u` 保证子进程每行即刷，转发不滞后
                print(line, end="", flush=True)
            rc = proc.wait()
        except KeyboardInterrupt:
            log("收到中断 —— 终止 worker 子进程")
            try:
                proc.kill()
            except Exception:
                pass
            raise
        if rc == HOT_RELOAD_EXIT:
            log("worker 代码已变更 —— 重新拉起子进程加载新代码（输出流不中断）")
            continue
        return rc


def _release_cloud_machine(
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """尽力真释放云机（§386，用户确认：worker 退出≠停机省钱）。

    worker 是 supervise_worker 拉起的**子进程**，不在 IPython kernel 里——
    `google.colab.runtime.unassign()` 需要 `get_ipython().kernel`，子进程里是 None。
    所以写哨兵文件，由 notebook cell 的 keepalive 循环（跑在 kernel 里）检测并执行 unassign。

    - Colab：写 /tmp/battle-halt-request 哨兵 → keepalive 检测 → kernel 里调 unassign()。
    - 其它（Kaggle 等）：无释放 API——诚实提示必须人工在宿主页面断开/关闭会话。
    任何失败都不抛（停机链路绝不能反过来崩 worker）。
    """
    sentinel = Path("/tmp/battle-halt-request")
    try:
        sentinel.write_text(str(time.time()))
        log("已写停机哨兵 /tmp/battle-halt-request（notebook keepalive 将检测并释放实例）")
    except OSError as e:
        log(f"写停机哨兵失败：{e}——请手工断开宿主会话")
    # 兼容：如果 worker 恰好跑在 kernel 里（单测 / 非 supervise 场景），直接试一次
    try:
        runtime_mod: Any = importlib.import_module("google.colab.runtime")
        ipython_mod = importlib.import_module("IPython")
        if ipython_mod.get_ipython() is not None:
            log("检测到 Colab kernel 环境 → 直接调用 runtime.unassign()")
            runtime_mod.unassign()
            return
    except Exception:
        pass

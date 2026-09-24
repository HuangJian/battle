"""remote/hub/boot.py — hub 的**引导链**：组装（`as_hub` / `make_server`）+ 进程入口（`main`）。

S4 第十六刀把 `remote/hub_server.py` 收口成薄入口时，这三件事搬到这里 —— 它们与 HTTP 面
（`hub/http_face.py`）是两种东西：HTTP 面对**每个请求**负责，引导链对**一次进程启动**负责。

```
main()            argparse（单课程 / 多课程 / --discover / --push）→ 课程表 → 起服务
 ├─ 单实例锁      remote._instance_lock（O_CREAT|O_EXCL，可接管陈旧锁）
 ├─ 双监听守卫    remote._port_guard（Windows SO_REUSEADDR 允许双绑 ⇒ bind 前探测）
 ├─ 课程表        --course 真源 / --discover 从盘上扫（+ 后台兜底线程按 DISCOVER_SCAN_SEC）
 ├─ push 派发     --push 才起（登记表来自 rl-config；缺省连探活线程都不起）
 └─ make_server   HubHandler 注入调度面 + token（**只挂上，不在此处起线程**）
```

⚠ 本模块**不**定义 `HubHandler`：它住 `hub/http_face.py`，`make_server` 只是把类属性
（`HubHandler.hub` / `.push`）指向新对象 —— ThreadingHTTPServer 每请求新建实例，共享面只能挂
类属性上，所以「谁往类属性上写」必须只有一处（这里）。

⚠ 层号：本模块 import `hub.http_face`（L5）⇒ **L6**。它**不**被 `remote/hub/*` 反向 import
（`hub/` 是「实现」侧，本模块是「装配」侧）；`remote/hub_server.py` 是本模块的**消费者**
（薄入口 + 门面 re-export），所以它 L7。
"""

from __future__ import annotations

import argparse
import atexit
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from common.protocol import (
    CLAIM_TTL_SEC,
    PUSH_POLL_SEC,
    PUSH_TIMEOUT_SEC,
    ProtocolError,
    parse_course_arg,
)

# 单实例锁 / 端口守卫（2026-09-17 双实例事故的两道闸）：与调度面无关，纯进程生命周期。
from remote._instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from remote._port_guard import ensure_port_free
from remote.hub.http_face import HubHandler
from remote.hub.queue import _HubQueue
from remote.hub.store import _JobStore
from remote.push_dispatch import (
    DEFAULT_PUSH_CONFIG,
    PushDispatcher,
    PushWorkers,
)

# ------------------------------------------------- 多课程调度面（常量）
#
# 调度面本体（`_HubQueue` 组合类 + 七个域混入）在 S4 第十五刀搬到了 `remote/hub/queue.py`
# 与 `remote/hub/queue_*.py`；`_MISSING_ROOT` 随 `_job_dir` 进了 `queue_observe`。
# 本模块只留**启动参数**用的常量。`_HubQueue` 走自别名 re-export
# （`hs._HubQueue` 是约 10 个测试与 e2e 的取名字入口——名字是契约，位置不是）。

#: 自动发现（`--discover`）的扫描节拍（秒）。派发热路径也会顺带扫（有最小间隔闸），
#: 这里只是「没人轮询时」的兜底：后台线程按这个节拍把新课程登记进来。
DISCOVER_SCAN_SEC = 5.0

def as_hub(store_or_hub: _JobStore | _HubQueue) -> _HubQueue:
    """把单个 `_JobStore` 包成单课程队列（幂等）。

    为什么保留这层：`make_server(store, ...)` 是既有测试与 e2e 夹具的**唯一**入口
    （十多个文件直接构造 `_JobStore` 再起 server）。不包装就要改十几个测试，而「多课程」
    本身不需要他们改一行 —— 单课程队列就是 `_HubQueue` 的退化情形，行为逐字节等价。
    """
    if isinstance(store_or_hub, _HubQueue):
        return store_or_hub
    return _HubQueue({"": store_or_hub}, order=[""])

def make_server(
    store_or_hub: _JobStore | _HubQueue,
    port: int,
    token: str,
    host: str = "0.0.0.0",
    *,
    hub: _HubQueue | None = None,
    push: PushDispatcher | None = None,
) -> ThreadingHTTPServer:
    """构造 server（handler 注入调度面 + token）。

    `store_or_hub` 兼容两种：单一 `_JobStore`（自动包成单课程队列，旧调用零改动）或
    已装配好的 `_HubQueue`（多课程；也可用 `hub=` 显式传）。

    host 默认 0.0.0.0（2026-09-16）：Tailscale 直连时云 worker 从 tailnet 入站访问，
    绑 127.0.0.1 会导致对端超时。单测/冒烟需回环时显式传 host="127.0.0.1"。
    """

    class Server(ThreadingHTTPServer):
        def __init__(self) -> None:
            super().__init__((host, port), HubHandler)
            self.token = token

    HubHandler.hub = hub if hub is not None else as_hub(store_or_hub)
    # push 派发器（可选）：只挂上，**不在此处启动** —— 起线程是启动器的动作，
    # 免得每个「只想拿个 server 发请求」的测试都被意外拉起一个后台拍。
    HubHandler.push = push
    return Server()

def main() -> None:
    ap = argparse.ArgumentParser(description="hub-server: remote PPO job queue (stdlib)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", default="", help="Bearer token（云 worker 与训练主循环共享）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    # 单课程（旧形状）：job-root/jsonl 直接给。既有测试、既有 dashboard 调用零改动。
    ap.add_argument(
        "--job-root", default="", help="job 目录根（单课程；多课程用 --course + --traj-root）"
    )
    ap.add_argument(
        "--jsonl", default="", help="training_log.jsonl 路径（单课程；多课程自动派生）"
    )
    # 多课程（2026-09-18 用户指令：一个 hub 进程服务所有并行课程）：
    #   --course tiny-a --course x1-rebirth-a2=offline ...
    #   派生 job-root=<traj-root>/<course>/remote-jobs、jsonl=<traj-root>/<course>/training_log.jsonl
    #   —— **磁盘布局与每课程一个 hub 时逐字节相同**，所以控制台/诊断工具/tmp/<course> 约定全照旧。
    ap.add_argument(
        "--course",
        action="append",
        default=[],
        metavar="NAME[=online|offline]",
        help="课程（可重复）。offline = 不实时派发 PPO、只收回传（整段自主的 kind=run 课）",
    )
    ap.add_argument(
        "--traj-root",
        default="tmp",
        help="--course/--discover 时的每课程目录根（相对 cwd 或绝对路径）；缺省 tmp",
    )
    # 共享 hub（2026-09-18 用户指令：hubserver 只开一个进程就同时支持所有并行课程）：
    # 课程表从盘上自动发现——训练侧发布 job 就是"这门课在跑"的事实，不需要第二事实源。
    ap.add_argument(
        "--discover",
        action="store_true",
        help="课程表从 --traj-root 自动发现（扫 <root>/*/{remote-jobs,offline}，新鲜窗口内"
        "自动登记）——新增/结束课程无需重启 hub、无需注册",
    )
    ap.add_argument(
        "--discover-sec",
        type=float,
        default=DISCOVER_SCAN_SEC,
        help=f"自动发现的扫描节拍（秒；缺省 {DISCOVER_SCAN_SEC:g}）",
    )
    ap.add_argument(
        "--lock-file",
        default="",
        help="单实例锁路径（缺省 nn-training/.hub_server.<port>.lock；按端口键控）",
    )
    ap.add_argument(
        "--push",
        action="store_true",
        help="启用 hub 中介 push 派发：按队列顺序把 job 推给登记在册的空闲 GPU worker"
        "（缺省关：不启用时连探活线程都不起，行为与改造前逐字节一致）",
    )
    ap.add_argument(
        "--push-config",
        default="",
        help=f"push worker 登记来源（rl-config 形状；缺省 {DEFAULT_PUSH_CONFIG}）",
    )
    ap.add_argument(
        "--push-poll-sec", type=float, default=PUSH_POLL_SEC, help="派发/结果轮询节拍（秒）"
    )
    ap.add_argument(
        "--push-timeout-sec",
        type=float,
        default=PUSH_TIMEOUT_SEC,
        help="单份 job 推送后的兜底上限（秒）；超时回落队首换 worker",
    )
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[hub-server] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print("[hub-server] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    # §单实例锁（2026-09-17，第二道闸）：端口守卫是「探测 → bind」的 TOCTOU —— 两个
    # starter 同时探测会双双通过（Windows 的 SO_REUSEADDR 还允许双绑，后启动者静默
    # 变僵尸）。锁用 O_CREAT|O_EXCL 把启动串行化，且能在**持有者身份可核验**的前提下
    # 自动接管陈旧锁（PID 复用 / 崩溃残留），不再出现「锁在、进程没了、永远启不来」。
    lock_path = args.lock_file or default_instance_lock_path("hub_server", args.port)
    if not acquire_instance_lock(lock_path, marker="hub_server", tag="hub-server"):
        sys.exit(1)
    atexit.register(release_instance_lock, lock_path)
    # §双监听守卫：Windows SO_REUSEADDR 允许双绑同端口（后启动者静默变僵尸）——
    # bind 前探测，端口已有活监听者即拒绝启动（2026-09-09 8787 双实例事故）。
    # 通配地址（0.0.0.0 / :: / ""）没有可连的语义 ⇒ 统一探回环，避免 0.0.0.0 在
    # Windows 上直接 WSAEADDRNOTAVAIL 而让守卫形同虚设。
    _probe_host = args.host if args.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    try:
        ensure_port_free(_probe_host, args.port)
    except RuntimeError as e:
        print(f"[hub-server] ERROR: {e}", flush=True)
        sys.exit(1)
    # ---- 课程表：--course 优先；两者都给 = 响亮拒启（不知道听谁的比听错好）----
    if (args.course or args.discover) and (args.job_root or args.jsonl):
        print(
            "[hub-server] ERROR: --course/--discover 与 --job-root/--jsonl 不能同时给"
            "（前者=多课程，后者=单课程）",
            flush=True,
        )
        sys.exit(1)
    hub: _HubQueue
    traj_root = Path(args.traj_root).resolve()
    if args.course or args.discover:
        specs: dict[str, str] = {}
        for raw in args.course:
            try:
                name, mode = parse_course_arg(raw)
            except ProtocolError as e:
                print(f"[hub-server] ERROR: --course {raw!r}: {e}", flush=True)
                sys.exit(1)
            if name in specs and specs[name] != mode:
                print(
                    f"[hub-server] ERROR: 课程 {name!r} 被重复声明且模式不同（{specs[name]} vs {mode}）",
                    flush=True,
                )
                sys.exit(1)
            specs[name] = mode
        root = traj_root
        stores = {
            name: _JobStore(
                root / name / "remote-jobs",
                root / name / "training_log.jsonl",
            )
            for name in specs
        }
        hub = _HubQueue(
            stores,
            order=list(specs),
            modes=specs,
            discover_root=root if args.discover else None,
        )
        desc = ", ".join(f"{c}:{specs[c]}" for c in specs)
        print(
            f"[hub-server] courses={len(specs)} [{desc}] traj_root={root} "
            f"discover={bool(args.discover)} offline={hub.offline_courses() or '-'}",
            flush=True,
        )
        if args.discover:
            # 后台节拍只是「没人轮询（push 模式 / 无 worker）」时的兜底：pull 路径的
            # `claim_next` 自己会先扫一次（带最小间隔闸），不让新课程等一个节拍。
            def _scan_loop() -> None:
                while True:
                    time.sleep(max(1.0, float(args.discover_sec)))
                    try:
                        hub.discover()
                    except Exception as e:  # 扫描失败不该让调度面死掉
                        print(f"[hub-server] discover 扫描失败: {e}", flush=True)

            Thread(target=_scan_loop, daemon=True, name="hub-discover").start()
    else:
        if not args.job_root or not args.jsonl:
            print(
                "[hub-server] ERROR: 需要 --course/--discover（多课程）或 "
                "--job-root + --jsonl（单课程）",
                flush=True,
            )
            sys.exit(1)
        single = _JobStore(args.job_root, args.jsonl)
        hub = as_hub(single)
    # push 派发（可选，2026-09-18）：登记表来自 rl-config 的 gpu_push 节点（控制台的
    # worker 登记入口回写它），派发器每拍探活 + 按队列顺序推给空闲 worker。
    push_disp: PushDispatcher | None = None
    if args.push:
        push_workers = PushWorkers(
            args.push_config or DEFAULT_PUSH_CONFIG,
            log=lambda m: print(f"[{time.strftime('%H:%M:%S')}] [hub-server push] {m}", flush=True),
        )
        push_workers.reload(force=True)  # 先读一次（缺文件/空表不致命，只是没人可推）
        push_disp = PushDispatcher(
            hub,
            push_workers,
            token,
            poll_sec=args.push_poll_sec,
            timeout_sec=args.push_timeout_sec,
            log=lambda m: print(f"[{time.strftime('%H:%M:%S')}] [hub-server push] {m}", flush=True),
        )
        print(
            f"[hub-server] push 派发已启用：登记表={push_workers.path} "
            f"候选 worker={len(push_workers.snapshot())} "
            f"poll={args.push_poll_sec:g}s timeout={args.push_timeout_sec:g}s",
            flush=True,
        )
    srv = make_server(hub, args.port, token, host=args.host, push=push_disp)
    if push_disp is not None:
        push_disp.start()
    print(
        f"[hub-server] listening on {args.host}:{args.port} "
        f"courses={hub.courses()} claim_ttl={CLAIM_TTL_SEC}s",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if push_disp is not None:
            push_disp.stop()

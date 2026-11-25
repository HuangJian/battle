"""hub/boot.py — hub 的**引导链**：组装（`as_hub` / `make_server`）+ 进程入口（`main`）。

S4 第十六刀把 `hub/server.py` 收口成薄入口时，这三件事搬到这里 —— 它们与 HTTP 面
（`hub/http_face.py`）是两种东西：HTTP 面对**每个请求**负责，引导链对**一次进程启动**负责。

```
main()            argparse（单课程 / 多课程 / --discover / --push）→ 课程表 → 起服务
 ├─ 单实例锁      common.instance_lock（O_CREAT|O_EXCL，可接管陈旧锁）
 ├─ 双监听守卫    common.port_guard（Windows SO_REUSEADDR 允许双绑 ⇒ bind 前探测）
 ├─ 课程表        --course 真源 / --discover 从盘上扫（+ 后台兜底线程按 DISCOVER_SCAN_SEC）
 ├─ push 派发     --push 才起（登记表来自 rl-config；缺省连探活线程都不起）
 └─ make_server   HubHandler 注入调度面 + token（**只挂上，不在此处起线程**）
```

⚠ 本模块**不**定义 `HubHandler`：它住 `hub/http_face.py`，`make_server` 只是把类属性
（`HubHandler.hub` / `.push`）指向新对象 —— ThreadingHTTPServer 每请求新建实例，共享面只能挂
类属性上，所以「谁往类属性上写」必须只有一处（这里）。

⚠ 层号：本模块 import `hub.http_face`（L5）⇒ **L6**。它**不**被 `hub/*` 反向 import
（`hub/` 是「实现」侧，本模块是「装配」侧）；`hub/server.py` 是本模块的**消费者**
（薄入口 + 门面 re-export），所以它 L7。
"""

from __future__ import annotations

import atexit
import importlib.util
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import common.runtime

# 单实例锁 / 端口守卫（2026-09-17 双实例事故的两道闸）：与调度面无关，纯进程生命周期。
from common.cli import house_parser
from common.instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from common.logutil import tag_logger
from common.port_guard import ensure_port_free
from common.protocol import (
    CLAIM_TTL_SEC,
    PUSH_AHEAD_DEFAULT,
    PUSH_POLL_SEC,
    PUSH_TIMEOUT_SEC,
    ProtocolError,
    parse_course_arg,
)
from hub.http_face import HubHandler
from hub.offline_push import (
    OFFLINE_PUSH_POLL_SEC,
    OfflinePushDispatcher,
    OfflinePushSpec,
    OfflinePushWorkers,
)
from hub.queue import _HubQueue
from hub.store import _JobStore
from hub.worker_pulse import WORKER_PULSE_SILENCE_SEC, WORKER_PULSE_SWEEP_SEC, error_body
from remote.code_snapshot import ensure_cluster_snapshot
from remote.push_dispatch import (
    DEFAULT_PUSH_CONFIG,
    PushDispatcher,
    PushDispatchSpec,
    PushWorkers,
)

# ------------------------------------------------- 多课程调度面（常量）
#
# 调度面本体（`_HubQueue` 组合类 + 七个域混入）在 S4 第十五刀搬到了 `hub/queue.py`
# 与 `hub/queue_*.py`；`_MISSING_ROOT` 随 `_job_dir` 进了 `queue_observe`。
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
    offline_push: Any = None,
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
    # 离线 push 派发器（可选；契约同上：只挂上，启动是 `main` 的事）。
    HubHandler.offline_push = offline_push
    return Server()


#: hub 的日志出口。`common/logutil` 是**行格式的唯一来源**（`[HH:MM:SS] [tag] msg`）：
#: 迁移前本文件 26 处 `print(...)` 里，一半手拼 `time.strftime`、一半干脆没有时间戳
#: ——`hub-server.out` 里两套格式并存，事后按时间轴复盘会对不上（python.refactor.md §P0-6）。
#: `clock` 传**取值函数**（`lambda: time`）而不是 `time` 模块：`monkeypatch.setattr(hub.boot,
#: "time", 假钟)` 仍拦得到（`common/logutil.tag_logger` 的既有约定）。
_log = tag_logger("hub-server", lambda: time)
_PUSH_LOG = tag_logger("hub-server push", lambda: time)
_OFFLINE_PUSH_LOG = tag_logger("hub-server offline-push", lambda: time)


def _landing_deps_warn() -> None:
    """启动自检：**本进程能不能把回传的 delta 就地重建为整份**（缺 numpy/torch 就不行）。

    ★ 2026-10-23 事故（y31-null，28/28 轮全中）：hub 由 dashboard 用 uv 的**基础解释器**起
    （venv 的 site-packages 没挂进 PYTHONPATH）⇒ `hub/deltas.land_into_stage` 落地时
    `remote.weights_delta` 要 numpy、`remote.opt_standard` 要 torch ⇒ 每轮回传都静默退回原样
    `.delta`，**唯一痕迹是 42 MB 日志里的一行**，运维零感知；而链式 delta 里 it2 之后全部
    连锁「基准不在本机」。

    用 `find_spec` 而不是 `import`：torch 真导入要几秒 + 上 GB RSS，而 hub 的启动闭包**明确**
    不许拉它（`tests/remote/test_hub_import_graph.py` 进程级判据）。
    """
    missing = [n for n in ("numpy", "torch") if importlib.util.find_spec(n) is None]
    if not missing:
        _log("整份重建能力：OK（numpy/torch 可见）")
        return
    _log(
        f"WARN: 缺 {'/'.join(missing)} ⇒ **回传的 delta 不会被就地重建为整份**"
        "（只能原样落 `.delta`，`resume_anchor` 会跳过那些轮，账本里 `landed_fp` 会等于原件 sha）。"
        "链式 delta 还会连锁：it1 落不了，it2 起全部「基准不在本机」。",
    )
    _log(
        "WARN: 修法 —— 用 `nn-training/.venv` 的解释器启动，或把它的 site-packages "
        "挂进 PYTHONPATH（控制台侧 `hubServerSpec` 自 2026-10-23 起已自动挂；"
        "手工启动时 `PYTHONPATH=nn-training:$(…)/site-packages`）。",
    )


def _hub_parser():
    """`main` 的 argv 定义表（自 `main` 原样搬出；描述 / 默认值 / help 逐字保留）。"""
    ap = house_parser(description="hub-server: remote PPO job queue (stdlib)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", default="", help="Bearer token（云 worker 与训练主循环共享）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    # 单课程（旧形状）：job-root/jsonl 直接给。既有测试、既有 dashboard 调用零改动。
    ap.add_argument(
        "--job-root", default="", help="job 目录根（单课程；多课程用 --course + --traj-root）"
    )
    ap.add_argument("--jsonl", default="", help="training_log.jsonl 路径（单课程；多课程自动派生）")
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
    # worker 静默哨兵（2026-10-31，`hub/worker_pulse.py`）：hub 是唯一知道「谁还活着」的地方
    # ——云侧把整机收走时，云机上的日志与浏览器上报腿一起消失，只有 hub 的请求间隔还留着。
    ap.add_argument(
        "--worker-silence-sec",
        type=float,
        default=WORKER_PULSE_SILENCE_SEC,
        help=(
            f"worker 静默告警阈值（秒；缺省 {WORKER_PULSE_SILENCE_SEC:g}，0=关闭）——"
            "超时且本 episod 有轮询节奏 ⇒ 一条 err 进 /admin/errors（只观测）"
        ),
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
    ap.add_argument(
        "--push-ahead",
        type=int,
        default=PUSH_AHEAD_DEFAULT,
        help="预推深度：worker 在跑时先推几份进它的接单队列（0 = 只推空闲机，回退档）",
    )
    ap.add_argument(
        "--offline-push",
        action="store_true",
        help="启用离线 push 派发：hub 出站去离线 worker 的隧道派任务、拉每轮产物"
        "（与 --push 相互独立；两者可同时开）",
    )
    # 2026-10-25（慢用例）：离线派发器的调度拍此前写死 `OFFLINE_PUSH_POLL_SEC=10s`，
    # 而 `--push-poll-sec` 早就可调——两边本该同口径。提成 CLI 旋钮后，e2e 才能用
    # 秒级拍跑「派单 → 停用 ⇒ 释放」全链（旧值下光等一拍就是 10s）。**生产缺省不变**。
    ap.add_argument(
        "--offline-push-poll-sec",
        type=float,
        default=OFFLINE_PUSH_POLL_SEC,
        help=f"离线 push 派发器的调度拍（秒；缺省 {OFFLINE_PUSH_POLL_SEC:g}）",
    )
    return ap


def _build_hub_queue(args) -> _HubQueue:
    """课程表构建（自 `main` 原样搬出）：多课程/发现模式 或 单课程 store。"""
    # ---- 课程表：--course 优先；两者都给 = 响亮拒启（不知道听谁的比听错好）----
    if (args.course or args.discover) and (args.job_root or args.jsonl):
        _log(
            "ERROR: --course/--discover 与 --job-root/--jsonl 不能同时给"
            "（前者=多课程，后者=单课程）",
        )
        sys.exit(1)
    hub: _HubQueue
    traj_root = Path(args.traj_root).resolve()
    if args.course or args.discover:
        specs: dict[str, bool] = {}  # 课程名 → 启动参数里是否带过模式段（P1-3 的兼容标记）
        for raw in args.course:
            try:
                name, mode = parse_course_arg(raw)
            except ProtocolError as e:
                _log(f"ERROR: --course {raw!r}: {e}")
                sys.exit(1)
            if mode:
                # ★M4b / P1-3：课程模式已退役（课程不再区分在线/离线，接管 hold 才是唯一
                # 真源）。**保留解析、WARN + 忽略**（不 400，别 brick 老启动脚本）；
                # `/admin/courses` 行上留一个 `mode_ignored` 标记供排障。
                _log(
                    f"WARN: --course {raw!r} 带了模式 {mode!r}——"
                    "课程模式已退役（课程不再区分在线/离线），本次按无模式处理",
                )
            specs.setdefault(name, bool(mode))
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
            ignored_modes=[c for c, ignored in specs.items() if ignored],
            discover_root=root if args.discover else None,
        )
        desc = ", ".join(specs)
        _log(
            f"courses={len(specs)} [{desc}] traj_root={root} discover={bool(args.discover)}",
        )
        if args.discover:
            # 启动先**立刻**扫一次（force，绕开 `DISCOVER_SCAN_MIN_SEC` 最小间隔闸）：课程表
            # 是磁盘事实（<traj_root>/<course>/{remote-jobs,offline}），盘上已经有的课没有理由
            # 等一个节拍才登记。2026-09-29 实测：缺这一步时，控制台/worker 在启动后的第一个
            # 节拍窗口内看到的是**空课程表**（缺省节拍 5s；e2e 用例按 0.2s 起 hub 也要白等
            # 1s —— 读面 `/admin/queue` 自身不触发扫描，只有 pull 的 `claim_next`/后台线程会）。
            try:
                hub.discover(force=True)
            except Exception as e:  # 扫描失败不该让调度面死掉（与后台线程同策略）
                _log(f"启动发现扫描失败: {e}")

            # 后台节拍只是「没人轮询（push 模式 / 无 worker）」时的兜底：pull 路径的
            # `claim_next` 自己会先扫一次（带最小间隔闸），不让新课程等一个节拍。
            #
            # 两处节奏都不再被**隐式地板**盖住（2026-09-29，§14 坑 2 同型）：
            #   ① `max(1.0, discover_sec)` —— 写死 1s 地板让 `--discover-sec 0.2` 形同虚设；
            #   ② `discover()` 的 `DISCOVER_SCAN_MIN_SEC`（2s 最小间隔闸，为**派发热路径**减
            #      重扫而设）—— 兜底线程本就被 `discover_sec` 限流，再叠一层 2s 反而把
            #      「新课程多久被登记」变成 `max(discover_sec, 2s)`（实测用例：盘上已有课
            #      启动即登记变快了，但新发布的课反而要多等 1.5s）。所以兜底线程用 `force=True`
            #      —— 它的节拍就该**只是** `--discover-sec`（生产缺省 5s，不变）。
            def _scan_loop() -> None:
                while True:
                    time.sleep(max(0.05, float(args.discover_sec)))
                    try:
                        hub.discover(force=True)
                    except Exception as e:  # 扫描失败不该让调度面死掉
                        _log(f"discover 扫描失败: {e}")

            #: 发现扫描线程归 hub 域（`hub-discover-scan`）：进程活多久它活多久。
            common.runtime.Runtime(scope="hub-discover").thread(_scan_loop, name="scan", daemon=True)
    else:
        if not args.job_root or not args.jsonl:
            _log(
                "ERROR: 需要 --course/--discover（多课程）或 --job-root + --jsonl（单课程）",
            )
            sys.exit(1)
        single = _JobStore(args.job_root, args.jsonl)
        hub = as_hub(single)
    return hub


def _resolve_token(args) -> str:
    """token 来源：`--token` 或 `--token-file`；读失败 / 两者都没有 ⇒ 拒启。"""
    token: str = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            _log(f"ERROR: 读 --token-file 失败: {e}")
            sys.exit(1)
    if not token:
        _log("ERROR: 需要 --token 或 --token-file")
        sys.exit(1)
    return token


def _acquire_startup_guards(args) -> None:
    """启动前的两道进程闸：单实例锁 + 端口探测（两者失败都 `exit(1)`）。"""
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
        _log(f"ERROR: {e}")
        sys.exit(1)


def _ensure_code_snapshot() -> None:
    """集群代码快照（2026-10-10，plan/cluster-code-snapshot）：失败只 WARN。

    hub 是「会话」的第一个锚点候选：一启动就把当前工作区打成一份 code.zip，之后所有课程的
    所有 job（以及 `/code` 的 bootstrap 取件）都用它——「只要不重启，大家跑同一份代码」。
    **警告不致命**：hub 起不来的代价远高于一份旧代码；训练侧 publish 有兜底（缺快照 ⇒
    per-course 打包 + WARN），`/code` 拿不到快照时会 404 且措辞点名。
    """
    try:
        ensure_cluster_snapshot(anchor_kind="hub", log=_log)
    except OSError as e:
        _log(
            f"WARN: 集群代码快照未能建立（{e}）——训练侧将回落 per-course 打包",
        )


def _build_push_dispatcher(args, hub: _HubQueue, token: str) -> PushDispatcher | None:
    """push 派发（可选，2026-09-18）：登记表来自 rl-config 的 gpu_push 节点。"""
    if not args.push:
        return None
    push_workers = PushWorkers(
        args.push_config or DEFAULT_PUSH_CONFIG,
        log=_PUSH_LOG,
    )
    push_workers.reload(force=True)  # 先读一次（缺文件/空表不致命，只是没人可推）
    push_disp = PushDispatcher(
        PushDispatchSpec(
            hub=hub,
            workers=push_workers,
            token=token,
            poll_sec=args.push_poll_sec,
            timeout_sec=args.push_timeout_sec,
            push_ahead=args.push_ahead,
            log=_PUSH_LOG,
        )
    )
    _log(
        f"push 派发已启用：登记表={push_workers.path} "
        f"候选 worker={len(push_workers.snapshot())} "
        f"poll={args.push_poll_sec:g}s timeout={args.push_timeout_sec:g}s "
        f"ahead={max(0, args.push_ahead)} cooldown={push_disp.cooldown_sec:g}s",
    )
    return push_disp


def _build_offline_push_dispatcher(
    args, hub: _HubQueue, token: str
) -> OfflinePushDispatcher | None:
    """离线 push 派发（2026-10-24，plan/push-mode-notebooks §6.6 / W5）：与 GPU 共用登记表。

    未启用时也要**说话**：2026-10-24 真机踩的坑是离线 worker 注册成功后一直 idle，
    而 hub 日志里既没有「已启用」也没有「未启用」——两边都不说话。
    """
    if not args.offline_push:
        _log(
            "离线 push 派发**未挂载**"
            "（启动参数无 --offline-push）——离线 worker 注册进来也不会派单；"
            "控制台节点页「离线 push」面板会显示「hub 未启用」",
        )
        return None
    offline_workers = OfflinePushWorkers(
        args.push_config or DEFAULT_PUSH_CONFIG,
        log=_OFFLINE_PUSH_LOG,
    )
    offline_workers.reload(force=True)  # 缺文件/空表不致命：只是没人可派
    offline_disp = OfflinePushDispatcher(
        OfflinePushSpec(
            hub=hub,
            workers=offline_workers,
            token=token,
            port=args.port,
            poll_sec=float(args.offline_push_poll_sec),
            log=_OFFLINE_PUSH_LOG,
        )
    )
    _log(
        f"离线 push 派发已启用：登记表={offline_workers.path} "
        f"候选 worker={len(offline_workers.snapshot())} poll={args.offline_push_poll_sec:g}s "
        f"在途={len(offline_disp.assignments.snapshot())}",
    )
    return offline_disp


def _mount_worker_pulse(args, hub: _HubQueue) -> None:
    """worker 静默哨兵（2026-10-31，`hub/worker_pulse.py`）：只在**进程入口**起线程。

    不在 `make_server` 里起：e2e/用例起 hub 时会拿到默认阈值但不起后台线程 —— 观测面
    不该给夹具塞一条常驻线程（同 `--discover` 的后台节拍只在 main 起）。
    """
    if args.worker_silence_sec != WORKER_PULSE_SILENCE_SEC:
        HubHandler.configure_pulse(silence_sec=args.worker_silence_sec)
    if args.worker_silence_sec <= 0:
        return

    def _pulse_loop() -> None:
        while True:
            time.sleep(max(0.05, WORKER_PULSE_SWEEP_SEC))
            try:
                for ev in HubHandler.pulse_events():
                    body = error_body(ev, silence_sec=args.worker_silence_sec)
                    # 进 hub 的错误 ring（控制台 `/admin/errors` 读面自然呈现），并落一行
                    # 日志——后者是**事后**唯一能 grep 的痕迹（ring 只活
                    # `WORKER_ERROR_TTL_SEC`＝120s，控制台没在跑就没人接得住）。
                    hub.note_worker_error(body)
                    _log(
                        f"⚠ {body['reason']}（worker={body['worker']}，只观测，不影响派发）",
                    )
            except Exception as e:  # 观测面坏掉不该让进程死（同 discover 扫描失败）
                _log(f"静默哨兵扫描失败: {e}")

    #: 静默哨兵线程归 hub 域（`hub-worker-pulse-scan`）：只观测，没人等它。
    common.runtime.Runtime(scope="hub-worker-pulse").thread(_pulse_loop, name="scan", daemon=True)
    _log(
        f"worker 静默哨兵已挂载：阈值={args.worker_silence_sec:g}s "
        f"扫描={WORKER_PULSE_SWEEP_SEC:g}s ⇒ 静默事件进 /admin/errors（只观测）",
    )


def _serve(
    args,
    hub: _HubQueue,
    token: str,
    push_disp: PushDispatcher | None,
    offline_disp: OfflinePushDispatcher | None,
) -> None:
    """起 server → 挂上派发器 → 前台跑；退出（含 Ctrl-C）时按反序停派发器。"""
    srv = make_server(
        hub, args.port, token, host=args.host, push=push_disp, offline_push=offline_disp
    )
    if push_disp is not None:
        push_disp.start()
    if offline_disp is not None:
        offline_disp.start()
    _log(
        f"listening on {args.host}:{args.port} courses={hub.courses()} claim_ttl={CLAIM_TTL_SEC}s",
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if push_disp is not None:
            push_disp.stop()
        if offline_disp is not None:
            offline_disp.stop()


def main() -> None:
    args = _hub_parser().parse_args()
    token = _resolve_token(args)
    _acquire_startup_guards(args)
    # ★ 2026-10-23：**能力自检**放在一切服务活动之前（缺依赖时每一轮回传都会静默降级，
    # 而那行提示以前只在 42 MB 日志里）。非致命：缺 numpy/torch 只影响「delta → 整份」，
    # hub 的其余职责（派发/受理原件/账本）照旧 ⇒ 只 WARN，不退出。
    _landing_deps_warn()
    _ensure_code_snapshot()
    hub = _build_hub_queue(args)
    push_disp = _build_push_dispatcher(args, hub, token)
    offline_disp = _build_offline_push_dispatcher(args, hub, token)
    _mount_worker_pulse(args, hub)
    _serve(args, hub, token, push_disp, offline_disp)

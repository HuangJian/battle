"""Shared pytest fixtures for nn-training pure-logic tests."""

from __future__ import annotations

import ctypes
import os
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# tmp 目录机制（含 `tmp_path` 覆盖）住在根 conftest：两层共享、每进程只注册一次。
from conftest import tmp_dir_for

# ---- 回环流量不许走环境代理（2026-09-18 门禁实测：同一类用例红了两次）----
# 本机用户级环境带 HTTP_PROXY/HTTPS_PROXY，而 no_proxy 里写的是 `127.*` 这种**通配** ——
# Python 的 urllib.request.proxy_bypass() 只认 host == entry / *.suffix / .suffix，
# 于是每一次打到测试临时端口（127.0.0.1）的请求都被送进外部代理：多一跳，代理抖动时回 502
# 或直接连接被拒（test_offline_deliver 读到 502、test_multi_course_hub 读到 Errno 111 ——
# 两次被测服务自己的日志都好好的）。生产侧靠 `common/net_http.py` 兑（不依赖环境变量），
# 测试侧就没地补**精确主名**进 no_proxy：一行兜住所有裸 `urlopen` 的用例（8 个文件在用）。
_LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")
for _key in ("no_proxy", "NO_PROXY"):
    _have = [x.strip() for x in os.environ.get(_key, "").split(",") if x.strip()]
    for _host in _LOOPBACK_NO_PROXY:
        if _host not in _have:
            _have.append(_host)
    os.environ[_key] = ",".join(_have)

# ---- 幽灵 KeyboardInterrupt 免疫（2026-09-15）----
# 本机（zh-CN Windows）pytest 树会间歇收到**无人按键**的 CTRL_C_EVENT 控制台组广播：
# threading 等待处 KeyboardInterrupt、python 门禁 ~7s 即败且卡在 ~33-36% 段（2026-09-14
# 与 2026-09-15（升级 3.12 后）各复现，Git Bash / VS Code / agent 沙箱都中）。来源是
# Windows 控制台进程组语义（某子进程/工具对组广播 Ctrl+C），不是用户按键。
# 对策：SetConsoleCtrlHandler(NULL, TRUE) 让本进程忽略控制台 Ctrl+C/CTRL_BREAK 广播
# （调用方进程组内任意来源），幽灵再无法打断门禁。
# 代价：本 pytest 进程不再响应 Ctrl+C（硬停需 taskkill /T）；门禁很短（~25s），可接受。
# 逃生口：NN_NO_IGNORE_CTRL_C=1 恢复响应（调试长套件时的例外）。
if sys.platform == "win32" and os.environ.get("NN_NO_IGNORE_CTRL_C") != "1":
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)


#: 候选「死端口」（按顺序探测）。9/1/7 是传统 discard/echo，正常机器上没人监听。
_DEAD_PORT_CANDIDATES = (9, 1, 7, 45871, 45872, 45873)


def _port_refuses_fast(port: int) -> bool:
    """本机回环的该端口是否**当场拒绝**（`ECONNREFUSED`，<1ms）。

    ⚠ 「没人监听」不等于「连接被拒」：某些容器/防火墙把未监听端口做成**黑洞**（SYN 被丢，
    等到超时才报错）。2026-10-29 实测本仓容器：`127.0.0.1:9` **8.008s 超时**，而
    `hub/task_pack.TASK_PACK_TRIGGER_TIMEOUT_SEC` 就是 8.0s ⇒ 每一处「控制台不可达」降级
    都白等 8s（`--durations` 里 `test_task_pack_missing_is_actionable_404` /
    `test_end_it_reached_must_match_the_current_pack_identity` 各 8.02s，两处全在等它）。
    探一次 ~0ms（拒绝是即时的），换掉每秒级的空等。
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.05)
    try:
        sock.connect(("127.0.0.1", port))
    except ConnectionRefusedError:
        return True
    except OSError:  # 超时 / 被丢包 / 无路由 —— 都不是「当场被拒」
        return False
    finally:
        sock.close()
    return False  # 连上了 = 有服务在听，绝不能拿它当死桩


def _dead_console_url() -> str:
    """选一个**连接当场被拒**的回环端口当死桩（不变量见 `_port_refuses_fast`）。"""
    for _port in _DEAD_PORT_CANDIDATES:
        if _port_refuses_fast(_port):
            return f"http://127.0.0.1:{_port}"
    # 兜底：bind 一个随机端口再立刻 close ⇒ 回到「没人监听」（本进程内刚验证过可用）。
    import socket

    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _probe.bind(("127.0.0.1", 0))
        _port = int(_probe.getsockname()[1])
    finally:
        _probe.close()
    return f"http://127.0.0.1:{_port}"


#: 测试期的控制台地址（**死端口**：连接当场被拒）——见下面 `_production_isolation` 与
#: `tests/test_production_isolation.py`（2026-10-06 事故：门禁跑完，本机 `bun run dashboard` 的
#: 命令行刷出 21 行 `[action] autoOfflineHandoff …` —— 是测试里的真 hub 子进程打过去的）。
#: 具体端口**按机器探测**（见 `_dead_console_url`）：固定写 `9` 在某些容器里是黑洞端口，
#: 「不可达」就会退化成「等满 8s 超时」。
TEST_CONSOLE_URL = _dead_console_url()

#: 测试期必须钉进**本用例自己的 `tmp_path`** 的生产状态面（env 名 → tmp 下的相对路径）。
#: 这些开关的缺省全都指向操作员正在用的活状态（`worker/gate_halt.py::intent_path` 等）：
#: 门禁意图能停全平台训练、循环控制能停真训练、`EVALBOARD_DATA` 是面板读的台账与心跳、
#: `BCITY_WEIGHTS_ARCHIVE_ROOT` 是面板权重选择器扫的真归档。
#: 2026-10-06 实测（不钉的后果）：门禁跑完，`dashboard/data/evalboard/runner_state.json`
#: 被 e2e 的真 `TrainingLoop` 写成了测试批次（`batch_id="bp"`）——操作员面板上出现幽灵 runner。
PRODUCTION_STATE_PINS: dict[str, str] = {
    "NN_GATE_HALT": "gate-halt.json",
    "NN_GATE_HALT_APPLIED": "gate-halt.applied.json",
    "NN_LOOP_CONTROL": "loop-control.json",
    "NN_LOOP_CONTROL_APPLIED": "loop-control.applied.json",
    "EVALBOARD_DATA": "evalboard/",
    "BCITY_WEIGHTS_ARCHIVE_ROOT": "weights-archive/",
}


def pin_production_env(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """把测试期的生产面钉进 `root`（唯一实现；两层 conftest 的 autouse 夹具都调它）。

    为什么必须有这一层：这些 env 的解析全是「设了就改用它，否则用生产缺省」。
    `hub/task_pack.py` 的控制台地址是「`BCITY_CONSOLE_URL` 为空 ⇒ `DEFAULT_CONSOLE_URL`
    = `http://127.0.0.1:8900`」（本机 dashboard 的默认端口，生产便利），于是测试里起的
    **真 hub 子进程**（夹具拷贝 `os.environ`）若漏设就打到**开发机上正在跑的控制台**，而
    `autoOfflineHandoff` 的动作本体会**动生产状态**：★M2 之前是
    `applyTrainModeToConfig(…, 'offline', {remember: true})`（控制台的**唯一配置写面**——一次
    测试就能把一门在开课的真课翻成离线停采）；★M2 起写面退役，但它仍会**导任务包**
    （起一次 python 进程 + 重写 `tmp/<课>/task-<课>.zip`）——课名撞上真课就是动了那门课的交付面。
    控制台地址钉**死端口**：漏网也只是「控制台不可达」的响亮降级，绝不碰真课。

    用 fixture 而**不是**模块级 `os.environ`：门禁是 `pytest tests/ e2e/` **同一进程**，
    模块级 setenv 会串味（同 `e2e/conftest.py::_no_serve_pool` 的教训）；`monkeypatch` 保证
    用例结束即还原。需要真控制台语义的用例（如 `e2e/test_auto_handoff_e2e.py` 的假控制台）
    自己设 `env["BCITY_CONSOLE_URL"]`，天然覆盖本桩。
    """
    monkeypatch.setenv("BCITY_CONSOLE_URL", TEST_CONSOLE_URL)
    # 补传腿的实现选择（2026-10-07）：缺省是**独立子进程**（`remote.deliver_worker`，
    # 2026-10-06 事故的隔离面）。单测不该为此起真子进程（慢、且会去碰真 hub 地址面）：
    # 钉成进程内线程模式，行为等价、无进程边界。真子进程只在
    # `tests/remote/test_offline_deliver_proc.py` 里显式 `mode="process"` 跑。
    monkeypatch.setenv("NN_DELIVER_MODE", "thread")
    # 同族课程产物直取（plan/episode-reuse.plan.md）：它会扫 `nn-training/tmp/**` 下真实的
    # 课程工作目录与 `curricula/*.jsonc`（找出同 level/难度的兄弟课）。不关就会让单测读出
    # 操作员在跑的课（既有污染面，也不 hermetic）⇒ 测试一律关，开关本身由专门用例钉。
    monkeypatch.setenv("NN_REUSE_SIBLINGS", "0")
    # 下行腿（预取下载/解包）同理（2026-10-20）：缺省是**独立子进程**
    # （`remote.payload_worker`，不抢训练进程的 GIL）。单测不该为预取起真子进程
    # （慢、且填充线程的 seam 全在进程内），钉成进程内模式；真子进程只在
    # `tests/remote/test_prefetch_proc.py` 里显式 `mode="process"` 跑。
    monkeypatch.setenv("NN_PREFETCH_MODE", "thread")
    for name, rel in PRODUCTION_STATE_PINS.items():
        # 只钉路径、**不预建**：真写点自己会 `mkdir(parents=True)`（`write_state` /
        # `write_intent` / `write_applied` 都这样），而预建目录会把「本用例的 tmp_path
        # 里应该只有哪些条目」的断言顶掉（2026-10-06 门禁实测：`test_write_applied_
        # shape_is_readable_by_the_console` 的 `list(tmp_path.iterdir()) == [f]` 红）。
        monkeypatch.setenv(name, str(root / rel))


@pytest.fixture(autouse=True)
def _production_isolation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """两层隔离的本体（另一层是 `e2e/conftest.py` 的同名夹具——e2e 是兄弟目录，不继承本文件）。

    用 `tmp_dir_for(request)` 而不是 `tmp_path` 形参：隔离只需要**路径**（真写点自己会
    mkdir）；请求 tmp_path 会为每个用例（哪怕从不写盘）建一个空目录。
    """
    pin_production_env(monkeypatch, tmp_dir_for(request))
    yield


@pytest.fixture(autouse=True)
def _no_observation_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """单测层**不发观测腿的真实 HTTP**（生命周期 + 错误上报；2026-11-01，与 `_production_isolation` 同族）。

    为什么（实测，不是猜）：`worker_loop` 每个会话打两条**同步**上报（`Lifecycle.start()`
    的「worker 启动」+ `close()` 的「收线」），而单测里的 hub 地址是 `http://hub` 这种
    **假主名** ⇒ 每一条都要白等一个真实的连接超时（本机实测 **1.41s/条** ⇒ 一次
    `worker_loop` **+2.8s**，与用例本身的工作量无关）。`--durations` 只会让人以为「这个
    用例很慢」，而它其实在等 DNS/connect（正是 conftest 头部说的「不占 CPU 的空等」）。
    2026-11-01 的 5 条 >5s 警告全部来自这里：`test_async_result_upload` ×2 /
    `test_backup_round_does_not_touch_computing_at` / `test_worker_loop_forwards_role` /
    `test_error_report::test_lifecycle_outcomes_are_not_reported`（内部跑 3 轮）。

    掐的只是**出口**（`remote/lifecycle.py` 里那两个模块全局），`Lifecycle` 本体照旧真跑：
    信号装卸、读数、读点节流、`why()` 都没被打桩。形状本身由
    `tests/remote/test_lifecycle.py` 直接钉（它自己 patch 这两个出口）。
    与 `pin_production_env` 把控制台地址钉成死端口同一条理由：观测面没有义务在单测里
    真的出网。e2e 层（真 hub 子进程）不继承本文件，行为零变化。
    """
    from remote import lifecycle

    monkeypatch.setattr(lifecycle, "post_error", lambda *a, **k: True, raising=True)
    monkeypatch.setattr(lifecycle, "report_error", lambda *a, **k: None, raising=True)
    # ② 错误上报腿的**按名导入的调用点**（`remote/job_round.py` / `remote/run_loop.py` 都是
    #    `from remote.error_report import report_error`，所以在源头打桩对它们无效）。源头模块
    #    本身**不**打桩：它自己的用例（`tests/remote/test_error_report.py`）要用真实现打真回环
    #    假 hub。两处都属「失败即丢」的观测腿 ⇒ 桩掉的只是网络，不是判据。
    #    2026-11-01 审计（一次性探针：包住 urllib/socket 计 >200ms 调用，跑完已删）：
    #    `test_worker_reap_stall` 一族的失败分支会在 **daemon 线程**里真发一笔到假 hub
    #    （2.0s = `WORKER_ERROR_TIMEOUT_SEC`），耗时被算到后续就绪的用例头上（`--durations`
    #    里是一串与它无关的用例，看起来毫无规律）。
    #    只桩**已导入**的模块：这层夹具每个用例都跑，不能为一个断言不到的调用点付一次 import
    #    （`remote/run_loop.py` 在部分收集下可能还没进来）。
    for _mod_name in ("remote.job_round", "remote.run_loop"):
        _mod = sys.modules.get(_mod_name)
        if _mod is not None:
            monkeypatch.setattr(_mod, "report_error", lambda *a, **k: None, raising=True)


def bp_args(
    sps: int = 3,
    rotate_stages: int = 35,
    total_stages: int = 35,
    seeds: str = "0-3",
    stages: str = "0-3",
) -> types.SimpleNamespace:
    """Minimal duck-typed args for build_pairs()."""
    return types.SimpleNamespace(
        rotate_stages=rotate_stages,
        seeds_per_stage=sps,
        total_stages=total_stages,
        stages=stages,
        seeds=seeds,
        curriculum_stages="",
        curriculum_start=4,
        curriculum_every=8,
        curriculum_grow=4,
        seed_rotate=0,
    )


@pytest.fixture
def worker_factory():
    """造假 push worker 并统一收尸（RUF013 友好：显式列表 + finally）。

    住在 conftest 而不是某个测试文件里：跨文件 `from tests.test_a import fixture`
    会让 ruff 判 `F811`（夹具名遮蔽模块级 import），而 conftest 的夹具**无需 import**
    就对所有用例可见。实现见 `tests/helpers/push_worker.py`。
    """
    from tests.helpers.push_worker import FakeWorker

    made: list[FakeWorker] = []

    def _make(**kw) -> FakeWorker:
        w = FakeWorker(**kw)
        made.append(w)
        return w

    yield _make
    for w in made:
        w.close()

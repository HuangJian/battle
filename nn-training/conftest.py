"""nn-training 根 conftest —— 全局 per-test **耗时预算**（2026-09-20；2026-09-26 抬高报错阈值）。

用户口径：单个测试 >5s 警告、>30s **报错**。理由（2026-09-20 实测）：一个安静的
26.5s 用例看起来「只是慢」，实际是三条线程停在 `all_settled.wait()` 上**纯空闲等**
生产超时（CPU 占用低、墙钟长）——耗时预算是抓这类退化的唯一廉价信号：不占 CPU 的
等待、真实的 sleep、串行化的网络超时，全都会在耗时上现形。

报错阈值为何是 30s（2026-09-26 用户口径）：本机时常被训练 rollout / 并行开发占满 CPU，
10s 会把「被抢 CPU 的正常用例」误判成退化（wall-clock 轮询类用例在 load 高时偶发超 10s、
单跑即绿）⇒ 该信号的信噪比被噪声污染。抬到 30s 后，抢 CPU 的抖动被放行，而 >5s 的警告
仍逐条留痕；真正的空等退化（本节 26.5s 级）依旧现形（先警告，>30s 才判失败）。

口径与环境变量（CLI 覆盖优先）：
  · 警告  5s   → `NN_TEST_WARN_S`（`--test-warn-s`）
  · 报错 30s   → `NN_TEST_FAIL_S`（`--test-fail-s`）
  · 个别用例确需更长 → 显式 `@pytest.mark.time_budget(seconds)`（**必须**在标记里
    写理由，见 pyproject markers 说明）。预算按「call 阶段」计（不含 fixture/收集）。
  · **CPU 密集用例不得配比本口径更紧的 `time_budget`**（2026-10-12 铁律）：预算抓的是
    **不占 CPU 的空等**，而纯扫描/纯计算的墙钟随本机负载漂 ~2.5×（实例：全仓锁重入扫描
    安静 6.2~6.9s、40 个忙循环下 16.26s）——配 15s 之类的紧预算只会制造负载假红。
    现场与实测阶梯 → `docs/nn/engineering.md` §77。

定位到根 conftest（而不是只放 tests/）是因为门禁跑 `pytest tests/ e2e/`：两层都要
被同一条规则覆盖。实现用 `pytest_runtest_makereport` 改写 outcome —— 超预算的用例
直接**判失败**（不是 teardown 报错），这样 `-x`、xdist、summary 全是标准语义。

第二项职责（2026-09-26）：**模块级 `check()`/`FAILS` 的静默绿必须变红**。一批从
standalone 脚本迁来的用例用 `FAILS: list[str]` + `check()` 累积失败，却只有脚本入口
`main()` 才 `sys.exit(1 if FAILS else 0)` —— 在 pytest 下 `check()` 只追加、无人断言
（全仓曾 `assert not FAILS` 零命中、9 个文件无 autouse 守卫）⇒ 整批用例永远绿。
见下方 `_no_silent_check_failures`。
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

# ---- CPU 内线程封顶（2026-10-09，docs/nn/engineering.md §68）----
# ⚠ 位置是语义的一部分：必须落在 numpy / torch **被 import 之前**（BLAS 在载入时就按
# 环境变量决定开几条线程、各留一份缓冲；事后 `torch.set_num_threads()` 只收 PyTorch 自己的
# 线程池，收不掉 numpy 侧已经 commit 出去的那部分）。
# 实测（本机 venv + torch 2.7.1+cpu，单进程**私有提交** PagefileUsage）：
#     不封    ：baseline 9MB → import numpy 500MB → +torch 642MB
#     封到 1  ：baseline 8MB → import numpy  17MB → +torch 159MB   （−75%）
# 而**工作集**两条路径几乎一样（178MB vs 177MB）⇒ 那 483MB 是「已 commit、未触碰」的
# BLAS 线程缓冲：任务管理器/工作集口径根本看不见，但它顶的就是 Windows 的 commit limit
# ——「系统内存紧张杀进程」（vscode 被误伤）的直接成因。xdist 下**每 worker 各付一份**。
# 为什么放根 conftest 而不只放门禁脚本：这里能**兜住所有 pytest 入口**（裸
# `python -m pytest` / CI / Makefile / WSL 侧 forkdist 的 worker），而门禁脚本已经
# export 过 ⇒ `setdefault` 不覆盖它。逃生口：NN_TEST_BLAS_THREADS=0（退回默认）。
_BLAS_THREADS = os.environ.get("NN_TEST_BLAS_THREADS", "1")
if _BLAS_THREADS != "0":
    for _blas_key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(_blas_key, _BLAS_THREADS)

import socketserver as _socketserver

import pytest

WARN_ENV = "NN_TEST_WARN_S"
FAIL_ENV = "NN_TEST_FAIL_S"
WARN_DEFAULT = 5.0
FAIL_DEFAULT = 30.0


def pytest_addoption(parser) -> None:
    group = parser.getgroup("nn-budget")
    group.addoption(
        "--test-warn-s",
        action="store",
        type=float,
        default=float(os.environ.get(WARN_ENV, WARN_DEFAULT)),
        help=f"单测耗时警告阈值（秒，缺省 {WARN_DEFAULT:g}；env {WARN_ENV}）",
    )
    group.addoption(
        "--test-fail-s",
        action="store",
        type=float,
        default=float(os.environ.get(FAIL_ENV, FAIL_DEFAULT)),
        help=f"单测耗时失败阈值（秒，缺省 {FAIL_DEFAULT:g}；env {FAIL_ENV}）",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    """门禁的 forkdist **在收集完成后** fork 出 worker ⇒ 这里先把「每个 worker 都会重算一遍」的
    整仓扫描算好，所有 worker 经 COW 直接命中缓存。

    为什么必须在这个钩子里（2026-10-29 实测）：模块级夹具（如
    `tests/test_remote_dag.py::_load_graph`）只在**执行相**跑，而分发是逐条动态派发的 ⇒ 同一份
    扫描被 **9 个 worker 各付一次**（0.35s 静默 / 满载 ~4s，合计占全套件 ~35s，且全在长尾上）。
    预热的代价只在 master 付一次（~0.35s 串行）。

    清单本身住 `tests/helpers/warmup.py`（加一项 = 加一行；**进/不进的判据**也在那里：只有一个
    worker 会付的扫描**不进**清单——那只是把成本从并行的执行相搬到串行的收集相）。
    每一项各自计时并打一行，某一项抛异常就跳过它（退回「每 worker 自算」），不影响其余项。

    xdist（Windows 门禁）下本钩子是空转：那边每个 worker 都是**新进程**且各自重新收集，master
    里的预热不共享 —— 非 forkdist 不预热，行为与以前一致。
    """
    if not session.config.pluginmanager.hasplugin("tools.forkdist"):
        return

    from tests.helpers import warmup

    # 传 items：判据要看收集到的测试源码（§104：只看 `sys.modules` 等于把前提押在
    # 「测试模块顶层 import torch」上，改懒就静默失效）。
    warmup.warm_all(session.items)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "time_budget(seconds): 显式放宽该用例的耗时预算（须附理由；缺省 5s 警告 / 30s 失败）",
    )


def _budget_of(item) -> tuple[float, float]:
    """该用例的 (warn, fail)；标记 `time_budget(N)` 可显式抬高。"""
    warn = item.config.getoption("--test-warn-s")
    fail = item.config.getoption("--test-fail-s")
    marker = item.get_closest_marker("time_budget")
    if marker is not None and marker.args:
        warn = fail = float(marker.args[0])
    return warn, fail


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # tmp 目录保留策略：setup/call/teardown 任一阶段报错 ⇒ 该用例的目录不删（供 debug）。
    # （与耗时预算共用一个 hook，避免同名实现被 ruff F811 判重。）
    if call.excinfo is not None:
        _TEST_BAD.add(item.nodeid)
    # 只算 call 阶段（fixture 建拆与收集不算）；已失败的用例不叠加噪音。
    if report.when != "call" or report.outcome != "passed":
        return
    warn, fail = _budget_of(item)
    secs = report.duration
    if secs > fail:
        report.outcome = "failed"
        report.longrepr = (
            f"耗时预算超限：本用例 call 阶段 {secs:.2f}s > {fail:g}s（用户口径 >30s 报错）。\n"
            f"墙钟长而 CPU 低的典型成因是纯空闲等待（生产超时/固定 sleep）——请改成"
            f"事件驱动，或用 policy 旋钮把配速调小（见 docs/nn/engineering.md §14）。\n"
            f"确需更长时用 @pytest.mark.time_budget(<秒数>) 显式放宽并写明理由。"
        )
    elif secs > warn:
        # 警告：不改 outcome，只在终端留痕（`-W` 类告警会淹在别处，用 warnings 更好检索）。
        import warnings

        warnings.warn(
            f"{item.nodeid} 耗时 {secs:.2f}s 超过警告阈值 {warn:g}s",
            stacklevel=1,
        )


# ── 全局：HTTP 测试服务的「关服」延迟（2026-09-29，§43；与 §14 同族）────────────────
# `BaseServer.serve_forever()` 的 `poll_interval` 缺省 **0.5s**，而 `shutdown()` 会
# **无条件等** `__is_shut_down`（没有超时）⇒ 「关服」最坏要等一整个轮询周期。仓库里约
# 40 处 `threading.Thread(target=srv.serve_forever, daemon=True)` 因此每条用例白付
# 0~0.5s：实测 `test_hub_push_dispatch::test_push_workers_admin_endpoint` 的 1.09s 里
# **1.005s 全在两次 `socketserver.shutdown()`**（`--durations` 只让人以为「这个用例很慢」）。
# 把默认轮询周期收到 10ms：只改「服务循环多久醒一次」，测试的判据全都挂在请求/事件上，
# 没人依赖 0.5s 的粒度；关服延迟从 ~0.25s 降到 ~5ms。显式传 `poll_interval=` 的调用点
# 不受影响（只换默认值，不改签名语义）。
_FAST_POLL_SEC = 0.01
_orig_serve_forever = _socketserver.BaseServer.serve_forever


def _serve_forever_fast_poll(self, poll_interval: float = _FAST_POLL_SEC) -> None:
    _orig_serve_forever(self, poll_interval)


# mypy 的 method-assign 是针对「改标准库方法」的通用警告：这里改的是**测试进程内**的默认
# 轮询粒度，包装器签名与语义同原函数（仍然转发 `poll_interval`），故收敛到这一行。
_socketserver.BaseServer.serve_forever = _serve_forever_fast_poll  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_silent_check_failures(request):
    """模块级 `check()`/`FAILS` 记下的失败必须让 pytest 变红（不许静默通过）。

    历史坑：`check()` 只往模块级 `FAILS` 追加，只有显式写了 `f0 = len(FAILS) … raise`
    的用例才会红（`e2e/test_run_rl.py` 先加过模块级 `_fail_loudly` 并记录了那次事故）；
    其余 9 个文件的 `check()` 在 pytest 下是**空转**（`assert not FAILS` 全仓零命中），
    其中 `test_rl_model` 是 0 真断言 / 10 个 check。把义务交给框架后，新增用例不可能再忘，
    也不需要每个文件各抄一份 fixture。
    standalone 入口（各文件 `main()`）不走 fixture，末尾的 `if FAILS: SystemExit(1)` 兜底。
    """
    fails = getattr(request.module, "FAILS", None)
    if not isinstance(fails, list):
        yield
        return
    before = len(fails)
    yield
    new = fails[before:]
    if new:
        raise AssertionError("check() 静默失败（模块级 FAILS 增长）: " + "; ".join(map(str, new)))


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """**skip 率上报表**（2026-10-09，`plan/nn-training-test-debt-cleanup.plan.md` §3-S3-3）。

    为什么要打：本机（Windows）门禁跑 `tests/ e2e/` 时有一批用例因**环境**恒 skip
    （POSIX-only / forkdist-only / 非 Linux）。它们不占墙钟、但也**一秒守卫都不提供**，
    而「本机门禁里到底有多少收集是被跳掉的」此前只能靠 `-rs` 逐条数。这里让恒 skip 的
    文件自己现形：一行总数 + 按文件分组的 top（T4 类要整文件挪走时，看这张表就够）。
    不计 `deselected`/`xfail`：前者不是「该跑没跑」，后者另有语义。
    """
    stats = getattr(terminalreporter, "stats", None)
    if not stats:
        return
    skipped = stats.get("skipped", [])
    counted = {"passed", "failed", "error", "skipped"}
    total = sum(len(v) for k, v in stats.items() if k in counted)
    if not total:
        return
    per_file: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for rep in skipped:
        per_file[rep.nodeid.split("::")[0]] = per_file.get(rep.nodeid.split("::")[0], 0) + 1
        why = (rep.longrepr or ("", "", ""))[2].split("\n")[0].strip() if rep.longrepr else ""
        key = why[:60] or "(no reason)"
        reasons[key] = reasons.get(key, 0) + 1
    pct = 100.0 * len(skipped) / total
    terminalreporter.write_sep("-", f"skip 率：{len(skipped)} skipped / {total} 收下（{pct:.1f}%）")
    for rel, n in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))[:12]:
        terminalreporter.write_line(f"  skipped {n:2d}  {rel}")
    for why, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
        terminalreporter.write_line(f"  ·×{n:<3d} {why}")


@pytest.fixture(autouse=True)
def _scratch_off(monkeypatch):
    """通用用例一律固定走 **scratch 回退档**（2026-10-09）。

    为什么必须钉：rollout / eval 要不要把热路径换到节点本地盘，由
    `common.scratch.resolve_scratch_root` 的**速度探针**当场决定（真写 8MB + fsync，判据
    「≥5× job 目录」）⇒ 同一份用例走哪一档是**机器速度 + 当时负载**的函数。2026-10-09
    门禁红现场：安静时本机 /dev/shm 只测出 2.6×（回退，绿）；`bun run pygate` 8 worker
    满载时 job 目录变慢 ⇒ 探针放行（换根档）⇒ `tests/remote/test_remote_iter.py` /
    `tests/worker/test_remote_serve_pool.py` 里那批「假导出器把 shard 平铺在 `--out` 之外」
    的用例转红（真导出器把 shard 写在 `--out` **里面**，见 `tools/sim/export-rl-rollout.ts`；
    `-x` 又只报第一条）。

    这与 `common/scratch.py` 的设计注记同源：「用例靠显式注入候选/env 才走 scratch ⇒
    **回退档是默认被回归覆盖的那一档**」—— 非 POSIX 上候选链本就为空，所以那句话在
    Windows 上白成立；POSIX 上必须由本 fixture 钉住（docs/nn/runtime-opt.md §34.2）。

    换根档本身由 `tests/remote/test_rollout_scratch.py` 用 `_only_candidate` + 常数探针
    **确定性**覆盖（30 例）；要 scratch 的用例自己 `setattr` 候选 / `setenv` env（后设的赢，
    monkeypatch 按反向顺序还原）。顺手清掉 env：操作员 shell 里带着 `NN_ROLLOUT_SCRATCH`
    时通用用例也不该悄悄改走换根档。
    """
    from common import scratch

    monkeypatch.delenv(scratch.SCRATCH_ENV, raising=False)
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", ())


# ── 测试临时目录：项目内、惰性创建、通过即清、失败保留（2026-09-14 用户定案）────────────
# 这一段原住 `tests/conftest.py`；e2e/conftest.py 为复用把两个 hook **import 进了自己的
# 命名空间**，而 pytest 会把 conftest 模块里每个 `pytest_*` 属性都注册成 hook 实现 ⇒
# 全量门禁（tests/ + e2e/）里每个 hook 跑两遍：同一份清理清单被两个 detached 进程抢删
# （`.cleanup.log` 里同 `(N dirs)` 的 spawn 成对出现、`removed N, skipped N` 互补），
# 「通过即清」因此长期欠账 —— 2026-11-11 实测残留 6116 个目录（6065 个不到 1 天）。
# 根 conftest 每进程只加载一次、两层共享，故把机制收在这里：没有第二份注册。
#
# 语义（用户口径）：
#   * 目录落在仓库根 `tmp/pytest-tmp/<nodeid>-<pid>-<ms>-<id>`（已 gitignore）；
#   * **惰性创建**：只有真请求 `tmp_path` 的用例才 mkdir。autouse 的
#     `_production_isolation` 只需要路径（真写点自己会 mkdir）—— 它此前依赖 tmp_path，
#     让每个用例（哪怕从不写盘）都留下一个空目录（实测占残留的一半以上：3586/6116）；
#   * 通过（含 skip）的目录在 session 收尾交给 `nn-clean-tmp.py` 子进程删除；
#     失败/报错的原样保留，供事后 debug（兜底由 KEEP_DAYS 窗口负责）。
#   * 删除必须走 `python -S` 子进程：进程内 rmtree 会被沙箱 sitecustomize 拦成
#     SystemExit（见 common/platform_utils 的平台说明），且不能阻塞 session 收尾。
_TMP_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "pytest-tmp"
_TEST_TMP_DIRS: dict[str, list[str]] = {}
_TEST_BAD: set[str] = set()


def tmp_dir_for(request) -> Path:
    """本用例的 tmp 目录路径（**只算不建**）；同一用例多次调用返回同一路径。

    缓存挂在 item 上：保证 autouse 的 `_production_isolation`（先跑）与真请求
    `tmp_path` 的用例看到**同一个**目录 —— 前者只把路径钉进 env，后者才 mkdir。
    """
    node = request.node
    cached: Path | None = getattr(node, "_nn_tmp_dir", None)
    if cached is not None:
        return cached
    safe = node.nodeid.replace("/", "__").replace("::", "__")
    # Windows 文件名净化（2026-10-02）：参数化 id 可以合法含 `:`（如 `[c5:]`、`[a:b]`）——
    # 直接用 nodeid 当目录名会 NotADirectoryError（WinError 267）。非法字符换成 `_`。
    safe = re.sub(r'[<>:"|?*\\]', "_", safe)
    # pid：分片并行 / forkdist 多进程时各进程的 id 序列相同，无 pid 会撞目录；
    # 毫秒：Windows PID 会复用，避免复用旧目录残留；item id：同进程内唯一。
    d = _TMP_ROOT / f"{safe}-{os.getpid()}-{int(time.time() * 1000)}-{id(node) & 0xFFFF}"
    node._nn_tmp_dir = d
    # 只登记**路径**（不建目录）：用例可能从不请求 tmp_path，但被钉进这个目录的 env
    # 会让生产代码自己 mkdir 写盘 —— 收尾时按「真实存在的目录」过滤，才能一并清掉。
    _TEST_TMP_DIRS.setdefault(node.nodeid, []).append(str(d))
    return d


@pytest.fixture
def tmp_path(request) -> Path:
    """覆盖内置 tmp_path：项目内唯一目录、零 basetemp（沙箱批量删除保护适配）。

    内置 tmp_path 依赖 pytest 的 basetemp——pytest 每次启动会**清空** basetemp
    （一次性删除全部累积文件，>50 触发沙箱 SAFE_DELETE_BULK_CONFIRM_REQUIRED：
    交互式弹确认、pre-commit hook 等无交互场景直接 SystemExit 失败）。

    本实现按需建目录、登记给 session 收尾清理（通过即删、失败保留），不创建 basetemp。
    """
    d = tmp_dir_for(request)
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def tmp(tmp_path: Path) -> Iterator[Path]:
    """Legacy test_run_rl.py compat：直接复用（被覆盖的）tmp_path。"""
    yield tmp_path


def pytest_sessionfinish(session, exitstatus):
    """session 收尾：把**通过**用例的临时目录清单交给清理器（失败/报错的不动）。

    ⚠️ **必须异步**（2026-09-14 实测踩坑）：一次全量约 800 个目录，Windows 上同步
    rmtree 每个都要几十~几百 ms —— 同步等它删完，session 收尾就变成几分钟（实测把
    门禁从 ~30s 拖到 215s+，提交被 hook 拦下）。清理是 best-effort 的收尾工作，**没有
    任何理由阻塞 session 结束**，故用 detached Popen 起进程、不等待：清单文件由清理器
    自己删（`nn-clean-tmp.py --paths` 用完即删）。

    2026-11-11：删除**不再带 10s 预算**（`NN_TMP_CLEAN_S=0`）。那个预算按「清理会阻塞」
    设计，但这里本来就是后台进程 —— 预算一到就停 = 每轮都留一部分，几次门禁就堆回几千个。
    POSIX 再加 `start_new_session`（握手/终端退出后不被 SIGHUP 带走），与 Windows 的
    DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP 对齐。
    """
    try:
        # 只收**真实存在**的目录：惰性创建的用例可能从未写盘（路径被登记但没 mkdir）。
        victims = [
            d
            for nodeid, dirs in _TEST_TMP_DIRS.items()
            if nodeid not in _TEST_BAD
            for d in dirs
            if os.path.isdir(d)
        ]
        if not victims:
            return
        print(
            f"[conftest] tmp 清理：{len(victims)} 个通过用例的目录入队"
            f"（登记 {len(_TEST_TMP_DIRS)} 个用例，其中 {len(_TEST_BAD)} 个需保留）",
            flush=True,
        )
        import subprocess
        import tempfile

        cleaner = Path(__file__).resolve().parents[1] / "tools" / "githook" / "nn-clean-tmp.py"
        if not cleaner.is_file():
            return
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            prefix="pytest-clean-",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write("\n".join(victims))
            list_path = fh.name
        try:
            from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

            # 比 CREATE_NO_WINDOW 更强：DETACHED_PROCESS 脱离父控制台，清理器
            # 既吃不到 git hook 控制台的幽灵 CTRL_C_EVENT，也不会向该控制台
            # 广播（2026-09-15 commit 卡死 / 幽灵 Ctrl-C）。CREATE_NO_WINDOW 在
            # DETACHED 下会被忽略，故再用 STARTUPINFO SW_HIDE 防黑窗闪烁。
            _flags = dict(_POPEN_NO_WINDOW)
            _si = None
            if sys.platform == "win32":
                _flags["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                )
                _si = subprocess.STARTUPINFO()
                _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                _si.wShowWindow = subprocess.SW_HIDE
            else:
                _flags["start_new_session"] = True

            log_path = cleaner.parent.parent.parent / "tmp" / "pytest-tmp" / ".cleanup.log"
            with log_path.open("a", encoding="utf-8") as log_fh:
                log_fh.write(f"--- spawn {list_path} ({len(victims)} dirs)\n")
                log_fh.flush()
                subprocess.Popen(
                    [sys.executable, "-S", str(cleaner), "--paths", list_path],
                    **_flags,
                    startupinfo=_si,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    env={**os.environ, "NN_TMP_CLEAN_S": "0"},
                )
        except Exception as e:
            print(
                f"[conftest] tmp 清理启动失败（留给门禁兜底）: {type(e).__name__}: {e}", flush=True
            )
            try:
                os.unlink(list_path)
            except OSError:
                pass
    except Exception:
        pass

"""remote/plan_run.py —— 半离线自主段（kind=run）的**执行引擎**（2026-09-23 从 `run_loop.py` 下沉）。

**为什么单独一层**：`remote/` 内部的依赖账本（`tests/helpers/remote_dag.py`）里，唯一被登记的
延迟环是 `run_loop ⇄ worker`——`worker.run_job` 在 kind=run 的尾巴上要 `verify_plan_file` /
`run_plan_job`，而 `run_loop` 每跑一轮又要回头调 `worker.run_job`。前者只能靠**下沉**消除：这两个
函数的传递闭包就是**整个执行引擎**（`RunContext` + 单轮执行 + 主循环，约 1100 行），所以引擎整块
搬到本模块，落在 `http` / `push_client` 同层的 L2。

**本模块不 import `remote.worker`**（守卫钉住）。「一轮怎么跑」通过 `RunContext.run_job_fn`
**注入**——原来那个 `run_loop._real_run_job` 兜底已删除：引擎不再替调用方决定用谁的 job 执行器。
调用方两侧各自注入自己那份：

  * `remote/worker.py`（kind=run 尾巴）：`run_plan_job(..., run_job_fn=run_job)`——把自己传进去；
  * `remote/run_loop.py`（CLI / 独立续跑）：传 `_real_run_job`（延迟 import `worker.run_job`）。

引擎语义与拆分前逐字一致（只改了这一处注入 + 块的位置）：hub 在交接时给一次（课程 + 初始权重 +
代码 + `plan.json`），此后云机自主把计划里的轮次跑完，每轮合成一个与 kind=iter **逐字段同构**的
job 再喂回 `run_job`——离线轮与 hub 监管轮走的是字面同一条代码路径。

链条的三个衔接点：① 对集 `rl.plan.pairs_for`；② argv `rl.plan.iter_spec`；③ 权重与 opt
（上一轮结果 → 下一轮 payload/`opt_sha`，Adam 动量不丢）。产物（`remote/artifacts.py`）是**唯一**
长期记录；补传（`remote/offline_deliver.py`）永远 best-effort、不阻塞、不抛。

★ 注入点：本模块里读的模块全局（`iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …）都是
**本模块命名空间**的——测试要拦它们请 patch `remote.plan_run.*`，patch `remote.run_loop.*` 打不着
（`run_loop` 只保留门面转发名，见 `tests/test_plan_run_split.py`）。
"""


from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from common.logutil import log_line
from common.protocol import (
    BLOB_DEMO,
    EVAL_SCRIPT,
    PAYLOAD_NAME,
    PLAN_NAME,
    ProtocolError,
    RetryableError,
    blob_path,
    decode_opt_tar,
    decode_weights_json,
    encode_opt_tar,
    encode_weights_json,
    iter_expected_data_fp,
    job_seed,
    normalize_manifest,
    pack_payload,
    validate_result,
)
from common.protocol import (
    job_id as make_job_id,
)
from log_bundle import LogBundle
from platform_utils import cpu_worker_slots
from remote.artifacts import (
    ArtifactStore,
    metrics_row,
    resolve_artifact_dir,
    sha256_bytes,
    sha256_file,
)
from remote.bundle import CODE_NAME as BUNDLE_CODE_NAME
from remote.offline_deliver import DRAIN_FLUSH_SEC, OfflineDeliverer, make_deliverer
from rl.plan import iter_spec, pairs_for, plan_pairs_fp, planned_iters, validate_plan

#: 产物目录里随段携带的 TS 运行时树（让「只下载产物 zip」的机器也能续跑）。
TS_TREE_DIR = "ts_code"
#: 任务包（全离线）里随包携带的两份运行时字节：python 代码快照与 TS 运行时 zip。
#: 有它们，节点可以**完全不联网**跑完整段（`run_job` 的 preloaded 直接吃这两份字节）。
TS_CODE_ZIP_NAME = "ts_code.zip"
#: 单轮的瞬时失败重试上限（自主模式没有 hub 兜底：重试够了就干净停下留产物）。
ITER_RETRIES = 2

#: 一轮评估**提交后**等它收线的时限（秒）——「交替」就是在这里做出来的。
#:
#: 为什么必须等（2026-09-25 云机卡死取证）：`_maybe_cloud_eval` 在 checkpoint 之后提交评估，
#: 而**下一轮的第一步就是 rollout**（`_run_with_retries` → `run_job` → `run_iter_rollout`），
#: 两条腿因此是**同时**各开满一份（96 核配额上 220+220；而那个 220 本身就是把宿主机报的
#: 224 核当成配额的产物，见 `platform_utils.effective_cores`）—— 旧注释那句「rollout 与 eval
#: 交替跑、互不预留」与代码事实不符。2× 超订把单局墙钟从 p90≈2.6s 推到 5s 硬顶之外 ⇒
#: 成批超时 ⇒ 池回退放大（一次超时 = 三份进程）⇒ 整轮停不下来。
#:
#: 维持在**有界**：评估永不把训练按住不放 —— 超时只记一行 WARN，训练照常继续（那时两条腿
#: 会重新交叠，日志里看得见）。真跑得慢的评估应降 `--eval-slots`/语料，而不是把等待调大。
EVAL_ALTERNATE_WAIT_SEC = 300.0


def _log_default(msg: str) -> None:
    """默认日志（tag=`run`）。行格式的唯一来源是 `common.logutil`。

    `clock=time` 是显式传的：本模块的 `time` 引用可被测试重绑（假钟注入），
    若改成让 logutil 自己抓 `time.strftime`，那些注入就失效了。
    """
    log_line("run", msg, clock=time)


# ------------------------------------------------------------------ 计划交接


def verify_plan_file(job_dir: str | Path, manifest: dict, *, log: Callable[[str], None] = _log_default) -> tuple[dict, str]:
    """payload 里的 `plan.json` → 校验 sha + 形状 + **全段对集指纹**。

    三道门，任何一道不过都**在跑第一局之前**响：
      ① sha256(plan.json) == manifest.plan_sha256（payload 损坏/串包）；
      ② `validate_plan` 形状（区间/硬上界/字段类型）；
      ③ `pairs_fp`：用 `build_pairs` 重放计划覆盖的**每一轮**对集，与 hub 发布时算的
         指纹逐位对账——`pair_args` 少一个字段、`build_pairs` 新增随机源，都在这里暴露。
         绝不在跑到半途才发现语料与计划不符（那时已经产生一批不可信 shard）。
    """
    p = Path(job_dir) / PLAN_NAME
    if not p.exists():
        raise ProtocolError(f"kind=run 的 payload 缺 {PLAN_NAME}（计划是自主段的唯一输入）——拒收")
    raw = p.read_bytes()
    sha = sha256_bytes(raw)
    want = str(manifest.get("plan_sha256", "") or "")
    if not want or sha != want:
        raise ProtocolError(
            f"plan.json sha256={sha[:16]}… != manifest.plan_sha256={want[:16]}…——拒收"
        )
    try:
        plan = validate_plan(json.loads(raw.decode("utf-8")))
    except UnicodeDecodeError as e:
        raise ProtocolError(f"plan.json 不是 UTF-8: {e}") from e
    if int(plan["start_it"]) != int(manifest["it"]):
        raise ProtocolError(
            f"计划 start_it={plan['start_it']} != manifest.it={manifest['it']}"
            "（计划与 job 不是同一轮的交接）——拒收"
        )
    got = plan_pairs_fp(plan)
    if got != str(plan.get("pairs_fp", "")):
        raise ProtocolError(
            f"计划对集指纹不符：重放={got[:16]}… 计划声明={str(plan.get('pairs_fp'))[:16]}…"
            "——pair_args 与 hub 侧不一致（拒收，未跑任何一局）"
        )
    log(
        f"计划校验通过：it{plan['start_it']} → it{plan['end_it']}"
        f"（{len(planned_iters(plan))} 轮，pairs_fp={got[:12]}…）"
    )
    return plan, sha


# ------------------------------------------------------------------ 运行上下文


class RunContext:
    """一段自主运行的全部句柄（计划 + 产物 + 传递下去的执行参数）。"""

    def __init__(
        self,
        *,
        plan: dict,
        plan_sha256: str,
        manifest: dict,
        store: ArtifactStore,
        work_dir: Path,
        device: Any = "cpu",
        torch_threads: int = 0,
        code_cache_dir: Path | None = None,
        ts_code_cache_dir: Path | None = None,
        # 全离线：随产物/任务包携带的两份字节（`run_job` 的 preloaded）——为空时
        # `run_job` 会回落去下载（在线链路），或直接报错（纯离线且未带包）。
        code_zip_bytes: bytes = b"",
        ts_code_zip_bytes: bytes = b"",
        course: Any = None,
        max_iters: int = 0,
        budget_sec: float = 0.0,
        #: 产物补传（「中途能连上 hub 就自动恢复在线回传」）：None = 这条腿没有补传
        #: （纯离线且没给 hub 地址，或用户显式关掉）——产物照常落本地。
        deliverer: OfflineDeliverer | None = None,
        run_job_fn: Callable[..., dict] | None = None,
        # ---- 云机 A 层评估（`eval_on_cloud`；见 `remote/offline_eval.py`）----
        #: True = 每 `eval_every` 轮在本机跑一遍 A 层语料（同 in-loop 口径，永不拖垮训练）。
        eval_on_cloud: bool = False,
        eval_slots: int = 0,
        eval_game_timeout_sec: float = 0.0,
        #: rollout 的并行局数（`--rollout-workers`）。**它覆盖计划里钉着的 `plan.workers`**：
        #: 后者是**导出那台机器**的规模（常在 8~16 核的本机导出，却要在 96 vCPU 的云机上跑），
        #: 而 rollout 与 eval 是（**且必须**）交替跑的 ⇒ 两者共用同一口径
        #: `platform_utils.cpu_worker_slots()`（用户 2026-09-22：「两者都使用 max(cores − 4,
        #: cores × 0.8)」）。「交替」由 `EVAL_ALTERNATE_WAIT_SEC` 那条有界等保证（见它）。
        #: 0 = 按本机核数自动。
        rollout_workers: int = 0,
        ts_tree_dir: Path | None = None,
        log: Callable[[str], None] = _log_default,
    ) -> None:
        self.plan = plan
        self.plan_sha256 = plan_sha256
        self.manifest = manifest
        self.store = store
        self.work_dir = Path(work_dir)
        self.device = device
        self.torch_threads = int(torch_threads or 0)
        self.code_cache_dir = code_cache_dir
        self.ts_code_cache_dir = ts_code_cache_dir
        self.code_zip_bytes = code_zip_bytes
        self.ts_code_zip_bytes = ts_code_zip_bytes
        #: 已加载的 CourseConfig（argv 重定向要按关卡取 stageJson；worker 侧在尾巴处已有）
        self.course = course
        self.max_iters = int(max_iters or 0)
        self.budget_sec = float(budget_sec or plan.get("budget_sec", 0.0) or 0.0)
        self.deliverer = deliverer
        self.run_job_fn = run_job_fn
        self.eval_on_cloud = bool(eval_on_cloud)
        self.eval_slots = int(eval_slots or 0)
        self.eval_game_timeout_sec = float(eval_game_timeout_sec or 0.0)
        self.rollout_workers = int(rollout_workers or 0)
        #: TS 运行时树的**实际根**（云机评估的 `cwd`：`tools/sim/export-eval-game.ts` 在那儿）。
        self.ts_tree_dir = Path(ts_tree_dir) if ts_tree_dir is not None else None
        #: 云机评估的后台执行者 / 口径（`_setup_cloud_eval` 装配；None = 不评估）。
        #: 执行者在**后台线程**里跑局（与下一轮 PPO 并行），所以这里的属性是可变引用。
        self.eval_runner: Any = None
        self.eval_plan: Any = None
        self.log = log
        self.t0 = time.time()
        #: 上一轮结果的 opt 原始字节的 sha（→ 下一轮 manifest.opt_sha，命中 blob_cache）
        self.last_opt_sha: str = ""

    def planned_range(self) -> list[int]:
        """本次实际要跑的轮次（计划区间 ∩ CLI 上限）。"""
        iters = planned_iters(self.plan)
        if self.max_iters > 0:
            iters = iters[: self.max_iters]
        return iters

    def budget_left(self) -> float:
        """剩余预算秒（`budget_sec <= 0` = 不限）。"""
        if self.budget_sec <= 0:
            return float("inf")
        return self.budget_sec - (time.time() - self.t0)

    # ---- 产物补传（best-effort；两个方法都**永不抛**，失败 = 产物停在本地）----

    def deliver_round(self, it: int) -> None:
        """一轮落盘后顺手把积压的轮次推给 hub（包含这一轮）。

        点与「落盘」同级是先后的：产物先自洽（`ArtifactStore.checkpoint`），再尽力出网
        ——反过来的话，一次慢网络就会把「本轮已完整落盘」这个事实推后到网络之后。

        **与 PPO 并行**（2026-09-22 用户指令）：后台模式下这里只是入队（非阻塞），真正的
        POST 发生在 `offline-deliver` 线程里；同步模式（`background=False`）保持旧行为。
        """
        if self.deliverer is None:
            return
        self.deliverer.submit_round(it)

    def deliver_final(self, *, it_end: int, state: str, summary: dict | None = None) -> None:
        """段末：补完积压 + 推一份摘要（跑到哪 / 什么状态 / 为什么停）。

        后台模式下同样只是入队，由 `close_delivery()` 做**有界** flush。
        """
        if self.deliverer is None:
            return
        self.deliverer.submit_final(it_end=int(it_end), state=str(state), summary=summary)

    def close_delivery(self, timeout: float = DRAIN_FLUSH_SEC) -> None:
        """段末收线：给后台补传线程有界时间把积压推完（同步/未启用时是空操作）。

        段末**必须**调（三个出口都要）：不调的话段末摘要还躺在队里，进程一退就没了。
        """
        if self.deliverer is None:
            return
        self.deliverer.close(timeout)


def open_run_context(
    *,
    plan: dict,
    plan_sha256: str,
    manifest: dict,
    job_dir: Path,
    work_dir: Path,
    artifacts_dir: str | Path | None = None,
    device: Any = "cpu",
    torch_threads: int = 0,
    code_cache_dir: Path | None = None,
    ts_code_cache_dir: Path | None = None,
    ts_tree: Path | None = None,
    code_zip_bytes: bytes | None = None,
    ts_code_zip_bytes: bytes | None = None,
    course: Any = None,
    max_iters: int = 0,
    budget_sec: float = 0.0,
    # ---- 云机 A 层评估（可选；见 `remote/offline_eval.py`）----
    eval_on_cloud: bool = False,
    eval_slots: int = 0,
    eval_game_timeout_sec: float = 0.0,
    rollout_workers: int = 0,
    # ---- 产物补传（可选；缺任一即关）----
    hub_url: str = "",
    hub_token: str = "",
    #: hub 侧的课程键（多课程 hub 的补传归位键；空 = 单课程 hub）。见 `OfflineDeliverer.course`。
    hub_course: str = "",
    deliver: bool = True,
    deliverer: OfflineDeliverer | None = None,
    run_job_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] = _log_default,
) -> RunContext:
    """建（或接上）产物目录 + 写出本段的起点 checkpoint，返回运行上下文。

    起点 checkpoint（`it-{start_it}`）= 本 job payload 里那份 init_weights / opt：**没有
    它产物目录就不自洽**（续跑要「最后一轮的权重」，而第一轮的输入正是上一段的产物——
    把它也写进来，产物才真正可以独立重放）。
    """
    root = resolve_artifact_dir(
        artifacts_dir, run_id=str(manifest.get("runId", "")), work_dir=work_dir
    )
    store = ArtifactStore(root, run_id=str(manifest.get("runId", "")), log=log)
    state = store.start(plan, manifest, plan_sha256=plan_sha256)
    ctx = RunContext(
        plan=plan,
        plan_sha256=plan_sha256,
        manifest=manifest,
        store=store,
        work_dir=work_dir,
        device=device,
        torch_threads=torch_threads,
        code_cache_dir=code_cache_dir,
        ts_code_cache_dir=ts_code_cache_dir,
        code_zip_bytes=(code_zip_bytes if code_zip_bytes is not None else _read_opt_file(root, BUNDLE_CODE_NAME)),
        ts_code_zip_bytes=(
            ts_code_zip_bytes
            if ts_code_zip_bytes is not None
            else _read_opt_file(root, TS_CODE_ZIP_NAME)
        ),
        course=course,
        max_iters=max_iters,
        budget_sec=budget_sec,
        eval_on_cloud=eval_on_cloud,
        eval_slots=eval_slots,
        eval_game_timeout_sec=eval_game_timeout_sec,
        # 缺省（0 = 自动）在这里解析：`open_run_context` 是唯一装配点，两条入口
        # （`--resume` 半离线 / 全离线包）都从这里过，解析一次就不会两边不一致。
        rollout_workers=(
            int(rollout_workers)
            if int(rollout_workers or 0) > 0
            else cpu_worker_slots()
        ),
        ts_tree_dir=_ts_tree_root(ts_code_cache_dir, manifest),
        deliverer=(
            deliverer
            if deliverer is not None
            else (
                make_deliverer(
                    hub_url=hub_url,
                    hub_token=hub_token,
                    run_id=store.run_id,
                    artifacts_dir=store.root,
                    course=hub_course,
                    log=log,
                )
                if deliver
                else None
            )
        ),
        run_job_fn=run_job_fn,
        log=log,
    )
    if ctx.deliverer is not None:
        ctx.deliverer.start()  # 后台并行（未启用/同步模式时是空操作）
        st = ctx.deliverer.status()
        log(
            f"产物补传已启用：{st['hub_url']}（已投递 {st['delivered']} 轮，"
            f"待投递 {st['pending']} 轮；{'后台与 PPO 并行' if st['background'] else '同步'}"
            "）——连不上就静默跳过，训练不受影响"
        )
    _setup_cloud_eval(ctx)
    last = int(state.get("last_it", plan["start_it"]))
    _seed_demo_blob_cache(
        manifest=manifest,
        job_dir=Path(job_dir),
        work_dir=Path(work_dir),
        artifacts_root=store.root,
        log=log,
    )
    _seed_start_checkpoint(ctx, job_dir=Path(job_dir), start_it=int(plan["start_it"]), last_it=last)
    _carry_ts_tree(ctx, ts_code_cache_dir=ts_code_cache_dir, ts_tree=ts_tree)
    return ctx


def _seed_demo_blob_cache(
    *,
    manifest: dict,
    job_dir: str | Path,
    work_dir: str | Path,
    artifacts_root: str | Path | None,
    log: Callable[[str], None] = _log_default,
) -> None:
    """run 启动期 demo bank 种子（x20 后续）：manifest.demo_sha 非空时，把 demo 字节
    落进 `work_dir/blob_cache/<sha>`（与 worker `_resolve_blob` 的缓存键同规），此后逐轮
    缓存命中、零传输。

    字节来源（按序）：产物目录 `demo.npz`（bundle 导入布局）→ job 目录 blob 文件
    （hub-run 路径 post() 落盘）。任一命中但 sha 不符 = 跳过找下一个；全无 ⇒ 启动期
    **响亮拒绝**（不等跑到 it1 的 PPO 才炸；修法写进错误里）。
    run 模式没有可用的 hub blob 通道（逐轮 manifest 是节点本地合成的，hub 上无此 job），
    所以这里**不**尝试联网下载——离线腿走 bundle（自动带包），手工递送请预置文件或缓存。
    """
    sha = str(manifest.get("demo_sha", "") or "")
    if not sha:
        return
    cache = Path(work_dir) / "blob_cache"
    dst = cache / sha
    if dst.is_file():
        try:
            if sha256_file(dst) == sha:
                log(f"demo bank 缓存命中（{sha[:12]}…）——零传输")
                return
            log("demo bank 缓存损坏（sha 不符）——重新种子")
        except OSError:
            pass
    cands: list[Path] = []
    if artifacts_root is not None:
        cands.append(Path(artifacts_root) / "demo.npz")
    cands.append(blob_path(job_dir, BLOB_DEMO))
    for c in cands:
        try:
            raw = c.read_bytes()
        except OSError:
            continue
        if sha256_bytes(raw) != sha:
            log(f"demo 候选 {c} sha 不符——跳过")
            continue
        cache.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(dst)
        log(f"demo bank 已种子进 blob_cache（{len(raw) / 1e6:.1f}MB，{sha[:12]}…）")
        return
    raise ProtocolError(
        f"manifest 要 demo bank（sha={sha[:12]}…）但节点侧无字节：产物目录缺 demo.npz、"
        "job 目录缺 blob.demo、blob_cache 未命中——修法：用带 demo 的任务包重导"
        "（export_bundle 有课程 demo_bank 即自动打包），或把 demo.npz 放进产物目录"
    )


def _read_opt_file(root: Path, name: str) -> bytes:
    """产物目录里的可选件字节（缺失 → 空：调用方按在线链路处理或响亮报错）。"""
    p = root / name
    try:
        return p.read_bytes() if p.is_file() else b""
    except OSError:
        return b""


def _ts_tree_root(cache_dir: Path | None, manifest: dict) -> Path | None:
    """TS 运行时树的根（评估器的 `cwd`）：`<cache>/<ts_code_sha256>`，缺 sha 时退回 cache 本体。

    内容寻址的 cache 布局是 `<cache>/<sha>/{tools/sim,src,...}`（`ensure_ts_cache_layout`）；
    云机评估最终要的是「有 `EVAL_SCRIPT`（`common/protocol.py`）的那个目录」，所以宁可在这里
    多探一层，也不把「相对路径 + bun 在 PATH」这条前提留在调用点靠猜。
    """
    if cache_dir is None:
        return None
    cache = Path(cache_dir)
    sha = str(manifest.get("ts_code_sha256", "") or "")
    cand = cache / sha if sha else None
    for p in (cand, cache):
        if p is not None and (Path(p) / EVAL_SCRIPT).exists():
            return Path(p)
    return cand if (cand is not None and Path(cand).is_dir()) else None


def _seed_start_checkpoint(ctx: RunContext, *, job_dir: Path, start_it: int, last_it: int) -> None:
    """把起点（`it-{start_it}`）补进产物目录（已有则不动）。"""
    if last_it > start_it or ctx.store.weights_path(start_it).exists():
        ctx.last_opt_sha = str(_stored_opt_sha(ctx, start_it) or "")
        return
    src = job_dir / "init_weights.json"
    if not src.exists():
        raise ProtocolError(
            f"缺起点权重 init_weights.json（自主段的起点无从落盘，拒收）：{src}"
        )
    opt = _opt_bytes_from_manifest(ctx)
    # 不记账本（row=None）：起点不是一轮训练。写进去会多出一条无 agg/report 的行，
    # 而「账本一行 = 一轮」是它给人看曲线的唯一契约（详见 ArtifactStore.checkpoint）。
    ctx.store.checkpoint(start_it, weights_json=src.read_bytes(), opt_tar=opt)
    ctx.last_opt_sha = sha256_bytes(opt) if opt else str(ctx.manifest.get("opt_sha", "") or "")
    ctx.log(
        f"起点已落盘：it{start_it}（{src.name}{' + opt' if opt else '，无 opt（Adam 从头）'}）"
    )


def _stored_opt_sha(ctx: RunContext, it: int) -> str:
    p = ctx.store.opt_path(it)
    return sha256_file(p) if p.exists() else ""


def _opt_bytes_from_manifest(ctx: RunContext) -> bytes:
    """job payload 带来的 opt（内联 base64 优先，其次内容寻址 blob 缓存）；无则空。"""
    inline = str(ctx.manifest.get("opt_init", "") or "")
    if inline:
        try:
            return decode_opt_tar(inline)
        except Exception as e:  # 损坏的内联 opt 不该让整段跑不起来（下场是动量归零）
            ctx.log(f"内联 opt 解码失败（{type(e).__name__}）——本段起点不带 Adam 动量")
            return b""
    sha = str(ctx.manifest.get("opt_sha", "") or "")
    if sha:
        for root in _blob_roots(ctx):
            p = root / sha
            if p.exists():
                return p.read_bytes()
    return b""


def _blob_roots(ctx: RunContext) -> list[Path]:
    """blob_cache 的候选根（worker 侧 = `code_root.parent/blob_cache`，code_root 缺省
    是 `work_dir/code_cache`）。"""
    roots: list[Path] = []
    if ctx.code_cache_dir is not None:
        roots.append(Path(ctx.code_cache_dir).parent / "blob_cache")
    roots.append(ctx.work_dir / "blob_cache")
    return roots


def _carry_ts_tree(ctx: RunContext, *, ts_code_cache_dir: Path | None, ts_tree: Path | None) -> None:
    """把 TS 运行时树拷进产物目录（一次，~几 MB）——产物因此**自洽可续**。

    为什么值得拷贝：续跑的唯一凭据就是这个目录；没有 TS 树，换台机器/新会话拿着 zip
    也跑不了 rollout（而 hub 可能早就没了，没处下载）。失败只记日志（不阻断训练）。
    """
    dst = ctx.store.root / TS_TREE_DIR
    if dst.is_dir():
        return
    src = Path(ts_tree) if ts_tree is not None else ts_code_cache_dir
    if src is None or not Path(src).is_dir():
        return
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
        ctx.log(f"TS 运行时已随产物携带：{dst}")
    except OSError as e:
        ctx.log(f"TS 运行时拷贝失败（续跑时需自带 --ts-root）: {e}")


# ------------------------------------------------------------------ 单轮执行


def with_rollout_workers(spec: dict, workers: int) -> dict:
    """把 rollout 规格的并行局数换成**本机**规模（`workers <= 0` = 原样不动）。

    为什么要换：计划里钉着的 `plan.workers` 是**导出那台机器**的规模（常在 8~16 核的本机导出，
    却要在 96 vCPU 的云机上整段跑），而 rollout 与云机 eval 是交替的 ⇒ 两者用同一个
    `platform_utils.cpu_worker_slots()`（用户 2026-09-22）。

    安全性：`workers` **不进** `data_fp`（`iter_declared_entries` 只取每条 argv 的 stage/seed），
    所以换并行度不会动摇任何指纹、也不改声明集。
    """
    n = int(workers or 0)
    if n <= 0 or int(spec.get("workers") or 0) == n:
        return spec
    return {**spec, "workers": n}


def _run_iteration(ctx: RunContext, it: int, *, prev_it: int) -> dict:
    """跑第 `it` 轮：合成 kind=iter job → 喂回 `run_job`（生产链路的字面复用）。

    `prev_it` = 上一轮（其产物权重就是本轮 init_weights）。返回本轮 result。
    """
    weights = ctx.store.weights_path(prev_it)
    if not weights.exists():
        raise ProtocolError(f"第 {it} 轮的 init 权重不在产物里: {weights}（产物被删/状态损坏？）")
    init_fp = sha256_file(weights)
    pairs = pairs_for(ctx.plan, it)
    spec = iter_spec(ctx.plan, it, pairs, wver=init_fp, course=ctx.course)
    spec = with_rollout_workers(spec, int(ctx.rollout_workers or 0))
    vol = ctx.plan.get("volume")
    # ★ 2026-09-24（日志节食）：这几行与 `run_job` 自己的入口/设备/装载读数是**同一个阶段**
    # （本轮上云从拼 spec 到 PPO 开算），所以攒进同一个 bundle，由 `run_job` 在装载完成时
    # 打**一行**（原来这里是 1 行 + 它那边 9 行）。
    prep_b = LogBundle(ctx.log)
    prep_b.add(
        f"it{it}",
        f"{len(pairs)} 局（{len({s for s, _ in pairs})} 关）wver={init_fp[:12]}…"
        + (
            f" 动态采集：每关初波 {vol['games_per_stage']} 局，训练侧配额 "
            f"{vol['per_stage_quota']}/关"
            if isinstance(vol, dict)
            else ""
        ),
    )
    prep_b.add(
        "动量", "带 Adam 动量" if ctx.last_opt_sha else "无 opt：Adam 从头"
    )
    # ---- 合成第 it 轮 manifest：与 hub 发布的 kind=iter 逐字段同构 ----
    m = dict(ctx.manifest)
    m.update(
        {
            "kind": "iter",
            "it": int(it),
            "rollout": spec,
            "init_weights_fp": init_fp,
            "data_fp": iter_expected_data_fp(spec),
            # opt 走内容寻址：上一轮 PPO 已把原始 tar 写进 blob_cache ⇒ 命中即零传输
            "opt_init": "",
            "opt_sha": str(ctx.last_opt_sha or ""),
            "opt_bytes": 0,
            "seed": job_seed(str(m["runId"]), int(it), init_fp),
        }
    )
    # 动态采集的**训练侧一半**：逐关严格样本量配额（`ceil(target/关数)`）必须逐轮随
    # manifest 走。hub 发布的头一份 manifest 里通常已经有它（`loop_steps._remote_ppo` 发布时
    # 算好），但计划才是自洽契约（包里/manual 导入的那份可能更旧）——按**计划**的值走，
    # 不一致时响亮记一笔（两个数不同 = 采集侧与训练侧对「目标」的理解已经漂了）。
    if isinstance(vol, dict):
        want = int(vol["per_stage_quota"])
        have = int(m.get("per_stage_quota", 0) or 0)
        if have and have != want:
            ctx.log(
                f"WARN it{it}: manifest per_stage_quota={have} 与计划 {want} 不一致"
                "——按计划走（采集量由计划反解）"
            )
        m["per_stage_quota"] = want
    m["job_id"] = make_job_id(m)
    m = normalize_manifest(m)
    # ---- 本轮 payload：只有 init_weights.json（shard 由节点现场产，与 kind=iter 一致）----
    it_dir = ctx.work_dir / m["job_id"]
    it_dir.mkdir(parents=True, exist_ok=True)
    zip_path = it_dir / PAYLOAD_NAME
    payload_init = it_dir / "init_weights.json"
    payload_init.write_bytes(weights.read_bytes())
    m["payload_sha256"] = pack_payload([], m, zip_path, extra_files=[payload_init])
    m = normalize_manifest(m)
    run_job = ctx.run_job_fn
    if run_job is None:  # 引擎不替调用方决定用谁跑一轮（原先这里兜底 worker.run_job）
        raise RuntimeError(
            "run_job_fn 未注入：半离线引擎必须由调用方给出 job 执行器"
            "（worker 侧传 run_job 自己；CLI 侧传 remote.run_loop._real_run_job）——"
            "见 remote/plan_run.py 头部"
        )
    # 代码快照与 TS 运行时：**有随包字节就用字节**（全离线：节点无仓、不联网），没有就
    # 交给 `run_job` 走它本来的路（命中 code_cache / 向 hub 下载——半离线轮就是这条路：
    # worker 自己那一轮已把 code.zip 落进内容寻址缓存）。两个来源都存在时优先字节。
    preloaded: dict = {"payload_zip": zip_path.read_bytes()}
    if ctx.code_zip_bytes:
        preloaded["code_zip"] = ctx.code_zip_bytes
    if ctx.ts_code_zip_bytes:
        preloaded["ts_code_zip"] = ctx.ts_code_zip_bytes
    result = run_job(
        "",
        "",
        {"job_id": m["job_id"], "manifest": m},
        work_dir=ctx.work_dir,
        device=ctx.device,
        torch_threads=ctx.torch_threads,
        preloaded=preloaded,
        code_cache_dir=ctx.code_cache_dir,
        ts_code_cache_dir=ctx.ts_code_cache_dir,
        prep=prep_b,
        log=ctx.log,
    )
    validate_result(result, m, commit_echo_must_match=False)
    raw = _opt_bytes_from_result(result)
    ctx.last_opt_sha = sha256_bytes(raw) if raw else ""
    return result


def _opt_bytes_from_result(result: dict) -> bytes:
    """`result.opt_tar_b64` → raw（链式传递的 Adam 动量）。缺/坏 → 空（下场：动量归零）。"""
    b64 = str(result.get("opt_tar_b64", "") or "")
    if not b64:
        return b""
    try:
        return decode_opt_tar(b64)
    except Exception:
        return b""


def _weight_bytes(result: dict) -> bytes:
    """`result.weights_json` → weights.json 字节。"""
    return bytes(decode_weights_json(str(result.get("weights_json") or "")))


def _checkpoint(ctx: RunContext, it: int, result: dict, *, wall_sec: float, phase: str = "train") -> dict:
    """把一轮结果落进产物目录（权重 + opt + 账本行），返回账本行。"""
    agg = dict(result.get("agg", {}) or {})
    report = result.get("report")
    wire = dict(result.get("wire", {}) or {})
    row = metrics_row(
        agg=agg,
        report=report if isinstance(report, dict) else None,
        wall_sec=wall_sec,
        ppo_sec=float(result.get("ppo_sec", 0.0) or 0.0),
        rollout_sec=float(wire.get("rollout_sec", 0.0) or 0.0),
        steps=int(agg.get("steps", 0) or 0),
        chunks=int(agg.get("chunks", 0) or 0),
        extra={"phase": phase} if phase != "train" else None,
    )
    entry = ctx.store.checkpoint(
        it, weights_json=_weight_bytes(result), opt_tar=_opt_bytes_from_result(result), row=row
    )
    ctx.log(
        f"it{it} 落盘：kl={agg.get('kl')} mean_ret={agg.get('mean_ret')} "
        f"winRate={(report or {}).get('winRate')} wall={row['wall_sec']}s → {ctx.store.dir_for(it)}"
    )
    ctx.deliver_round(it)  # 补传在落盘之后（先自洽，再尽力出网）
    return entry


def _setup_cloud_eval(ctx: RunContext) -> None:
    """建云机评估的**后台**执行者（与下一轮 PPO 并行）。无可评语料/环境不对 ⇒ 关掉，不拖训练。

    为什么不是「直接调 run_cloud_eval」：那是阻塞式（本机 100 局 × 0.8s ≈ 80s 占满一个
    迭代的墙钟），而用户口径与 trainer 一致——**eval 跑在 CPU 上，不该阻塞 PPO**。
    """
    if not ctx.eval_on_cloud:
        return
    try:
        from remote.offline_eval import CloudEvalRunner, default_slots, eval_plan_of
    except ImportError as e:  # rl 包不在（截断的代码快照）——响亮记一笔，不拖训练
        ctx.log(f"WARN: 云机评估不可用（{e}）——本段不评估")
        ctx.eval_on_cloud = False
        return
    try:
        # 语料口径从课程读；`rollout_workers` 只是**记录在案**（日志里报出来，便于对照）。
        ep = replace(eval_plan_of(ctx.course), rollout_workers=int(ctx.rollout_workers or 0))
    except Exception as e:
        ctx.log(f"WARN: 云机评估计划不可读（{type(e).__name__}: {e}）——本段不评估")
        ctx.eval_on_cloud = False
        return
    if not ep.enabled:
        ctx.log(
            "云机 A 层评估开着，但课程没配语料（eval_stages 空或 eval_games_per_stage=0）"
            "——本段不会真评"
        )
        ctx.eval_on_cloud = False
        return
    ctx.eval_plan = ep
    # 缺省并发在这里解析成**真实数字**（日志里就不是「缺省」两个字了）。口径与 rollout 相同
    # （`cpu_worker_slots`）：两者交替跑，谁也不为谁留核数。
    ctx.eval_slots = int(ctx.eval_slots) if int(ctx.eval_slots) > 0 else default_slots()
    ctx.log(
        f"云机 A 层评估已启用（**与下一轮 PPO 并行**，CPU）：每 {ep.eval_every} 轮 × "
        f"{len(ep.stages)} 关 × {ep.n_seeds} 种种子（diff={ep.difficulty}，并发 {ctx.eval_slots} 局"
        + (
            f"；rollout 并行 {ctx.rollout_workers}（同一口径、互不预留，但**本轮的评估跑完才开"
            f"下一轮 rollout**，最多等 {EVAL_ALTERNATE_WAIT_SEC:.0f}s）"
            if ctx.rollout_workers
            else ""
        )
        + "）——与 in-loop 同一份语料/行 schema；结果落产物目录的 eval_log.jsonl"
    )
    ctx.eval_runner = CloudEvalRunner(
        _eval_job_builder(ctx, ep), log=ctx.log, on_round_done=_eval_round_done(ctx)
    )


def _eval_round_done(ctx: RunContext) -> Callable[[int, dict], None]:
    """评估落账后的钩子：把这一轮**重投**一次（补上刚产生的 `eval_rows`）。

    时序事实：产物 POST 在落盘之后立刻发出（与 PPO 并行），而评估还在飞——所以第一次投递
    时这一轮的逐局行还不存在。hub 对重复投递幂等但**仍会并账** eval 行，所以重投一次就把
    读数补齐（否则云上评的读数只能等到段末 artifacts zip）。
    """

    def on_done(it: int, result: dict) -> None:
        if not (isinstance(result, dict) and result.get("ran")):
            return
        if ctx.deliverer is None:
            return
        ctx.deliverer.submit_eval_round(int(it))
        ctx.log(
            f"it{it} 评估落账 {result.get('settled')} 局——已请求重投该轮（补读数的幂等补传）"
        )

    return on_done


def _eval_job_builder(ctx: RunContext, ep: Any) -> Callable[[int], dict]:
    """`it` → `run_cloud_eval` 的关键字参数（**提交时才求值**：权重路径按当轮算）。

    权重用 `it-NNN/weights.json` 而不是任何「活指针」：那是这一轮不可变且已在盘上的
    W(it)，`wver` 因此与本地/节点评同一份权重时逐位相同（账本可配对）。
    """

    def build(it: int) -> dict:
        return {
            "plan": ep,
            "it": int(it),
            "weights_path": ctx.store.weights_path(int(it)),
            "eval_jsonl": ctx.store.root / ArtifactStore.EVAL_LOG_NAME,
            "ts_root": ctx.ts_tree_dir or "",
            "work_dir": ctx.work_dir / "eval-work",
            "course": ctx.course,
            "course_fp": str(ctx.manifest.get("course_fp", "") or ""),
            "slots": ctx.eval_slots,
            # **原样传 0（= 没指定）**：由 `run_cloud_eval` 按 `common/game_watch.py` 解析成
            # 「首次尝试 = 5s（用户口径：单局 >5s 肯定不正常）、重试 ×4」；显式给了正数就完全
            # 按用户给的数且不对重试放大（配置说了算）。不在这里提前解析，是因为「显式 vs
            # 兜底」这个区别决定了重试要不要放宽，解析一次就丢了这个信息。
            "game_timeout_sec": float(ctx.eval_game_timeout_sec or 0.0),
            "log": ctx.log,
        }

    return build


def _maybe_cloud_eval(ctx: RunContext, it: int) -> None:
    """该轮该评就把评估**丢给后台**（`eval_on_cloud`）：不阻塞下一轮 rollout/PPO。**永不抛**。

    到点的那一轮还要再看一眼「上一轮评完没」——这点观测在 `CloudEvalRunner.submit` 里
    （有界等一小会，仍不空闲就跳过本轮），所以这里只负责「该不该评」。

    **提交后要有界等它收线**（`EVAL_ALTERNATE_WAIT_SEC`）：评估与下一轮的 rollout 同时开跑
    就是「2× 超订 ⇒ 成批踩 5s 硬顶」的成因（见那个常量的注释）。等它跑完，「交替」才真的
    是交替；等超时也照常放行（评估永不按住训练）。
    """
    runner = getattr(ctx, "eval_runner", None)
    plan = getattr(ctx, "eval_plan", None)
    if not ctx.eval_on_cloud or runner is None or plan is None:
        return
    try:
        if not plan.due(it):
            return
        if not runner.submit(it):
            return  # 本轮没提交（上一轮还在飞）——`submit` 自己已记过一笔
        wait_idle = getattr(runner, "wait_idle", None)
        if wait_idle is not None and not wait_idle(EVAL_ALTERNATE_WAIT_SEC):
            ctx.log(
                f"WARN: 云机评估 it{it} 超过 {EVAL_ALTERNATE_WAIT_SEC:.0f}s 未收线 —— 训练继续"
                "（评估仍在后台，会与接下来的 rollout 抢 CPU；真要缩短就降 --eval-slots/语料）"
            )
    except Exception as e:  # 评估是旁路：任何意外都不能影响训练
        ctx.log(f"WARN: 云机评估提交失败（忽略）：{type(e).__name__}: {e}")


def runner_timeout(ctx: RunContext) -> float:
    """评估收线时限（秒）：没配就取 `CloudEvalRunner` 的缺省（600）。"""
    return float(getattr(getattr(ctx, "eval_runner", None), "drain_timeout_sec", 600.0) or 600.0)


def _close_eval(ctx: RunContext, *, timeout: float | None = None) -> None:
    """段末收线：给在飞的云机评估有界时间落账（三个出口都要调）。**永不抛**。

    不调的话，段末那一刻还在飞的局随进程一起消失，那一轮没有 summary——白评一轮
    （逐局行可能已落盘，但板子按 summary 画曲线）。
    """
    runner = getattr(ctx, "eval_runner", None)
    if runner is None:
        return
    try:
        cur = runner.inflight_it() or runner.pending_it()
        if cur is not None:
            limit = runner.drain_timeout_sec if timeout is None else float(timeout)
            ctx.log(
                f"段末收线：等云机评估 it{cur} 落账（在飞的局跑完即走，最多等 {limit:.0f}s）"
            )
        runner.drain(timeout)
    except Exception as e:
        ctx.log(f"WARN: 段末评估收线异常（忽略）：{type(e).__name__}: {e}")


# ------------------------------------------------------------------ 主循环


def run_plan_job(
    *,
    job_id: str,
    manifest: dict,
    job_dir: Path,
    work_dir: Path,
    plan: dict,
    plan_sha256: str,
    first_result: dict,
    course: Any = None,
    device: Any = "cpu",
    torch_threads: int = 0,
    code_cache_dir: Path | None = None,
    ts_code_cache_dir: Path | None = None,
    artifacts_dir: str | Path | None = None,
    max_iters: int = 0,
    budget_sec: float = 0.0,
    #: 云机 A 层评估（`eval_on_cloud`；见 `remote/offline_eval.py`）。
    eval_on_cloud: bool = False,
    eval_slots: int = 0,
    eval_game_timeout_sec: float = 0.0,
    #: rollout 并行局数（0 = 按本机核数自动；与 eval 同一口径，见 `RunContext.rollout_workers`）。
    rollout_workers: int = 0,
    #: 产物补传（可选）：给了 hub 地址 + token 就在每轮落盘后尽力推一份上去
    #: （hub 中途失联也不至于「跑完一整段、控制面一无所知」）。
    hub_url: str = "",
    hub_token: str = "",
    #: 本份产物在 hub 里的**归位键**（多课程 hub 的课程键；见 `OfflineDeliverer.course`）。
    #: 领活路径由 hub 在轮询面下发（`remote/worker.py` 传进来），全离线包那条腿
    #: 由 `--hub-course` 给。空 = 单课程 hub。
    hub_course: str = "",
    deliver: bool = True,
    run_job_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] = _log_default,
) -> dict:
    """从「首轮结果」续跑：`first_result` 已经跑完，接着把计划剩下的轮次跑完。

    ★ 2026-09-25：**今天没有生产调用者**。它曾经是「hub 发一份 `kind=run` 整段 job、
    worker 接着跑完」那条腿的尾巴——那条腿已退役（`plan/online-offline-role-routing.plan.md`
    §7：hub 侧不再发这种活，`remote/worker.py` 对它响亮拒收；离线课走任务包 + `run_standalone`）。
    保留它的理由只有一个，但很实在：它是把 `_drive` **从首轮结果续下去**的唯一入口，而
    `tests/test_run_loop.py` 的 4 组段语义回归（整段逐轮同构 / 锚点续跑不重复记账 /
    `max_iters` 上限 / 轮失败后产物仍可续）全挂在它上面——删它等于把这些回归一起删。

    返回**合并结果**（末轮形状 + `iters` 明细 + `it_end` + `artifacts`）。

    收尾策略：正常跑完、预算到点、还是中途抛错，都 `finalize` 产物（state + 全量 zip）
    之后再返回/抛出——**任何时刻停下，卷上的目录都是自洽可续的**。
    """
    ctx = open_run_context(
        plan=plan,
        plan_sha256=plan_sha256,
        manifest=manifest,
        job_dir=Path(job_dir),
        work_dir=work_dir,
        artifacts_dir=artifacts_dir,
        device=device,
        torch_threads=torch_threads,
        code_cache_dir=code_cache_dir,
        ts_code_cache_dir=ts_code_cache_dir,
        ts_tree=ts_code_cache_dir,
        course=course,
        max_iters=max_iters,
        budget_sec=budget_sec,
        eval_on_cloud=eval_on_cloud,
        eval_slots=eval_slots,
        eval_game_timeout_sec=eval_game_timeout_sec,
        hub_url=hub_url,
        hub_token=hub_token,
        hub_course=hub_course,
        deliver=deliver,
        run_job_fn=run_job_fn,
        log=log,
    )
    anchor_it = int(manifest["it"])
    session: list[dict] = []
    state = ctx.store.read_state() or {}
    # 接续点 = max(锚点, 产物里已到的那一轮)：同 job 重领时产物可能已经跑在前面
    # （会话中途被回收）——从产物接上，**不重跑也不重复记账**（账本是给人看的曲线，
    # 同一 it 两行会让它彻底读不了）。
    start_from = max(anchor_it, int(state.get("last_it", anchor_it)))
    stored = ctx.store.weights_path(anchor_it)
    if start_from > anchor_it:
        ctx.log(f"产物已跑到 it{start_from}——从产物接上（锚点 it{anchor_it} 不重复记账）")
        ctx.last_opt_sha = _stored_opt_sha(ctx, start_from) or str(ctx.manifest.get("opt_sha", "") or "")
    elif stored.exists() and sha256_file(stored) == sha256_bytes(_weight_bytes(first_result)):
        # 锚点轮产物与本次结果一致——沿用，不重复记账
        ctx.log(f"锚点 it{anchor_it} 已在产物里且与本次结果一致——沿用（不重复记账）")
        ctx.last_opt_sha = _stored_opt_sha(ctx, anchor_it) or str(ctx.manifest.get("opt_sha", "") or "")
    else:
        session.append(_checkpoint(ctx, anchor_it, first_result, wall_sec=0.0, phase="anchor"))
    return _drive(ctx, session=session, start_from=start_from)


def _drive(ctx: RunContext, *, session: list[dict], start_from: int) -> dict:
    """从 `start_from` 之后跑到计划末尾（受预算/上限约束），返回合并结果。"""
    todo = [it for it in ctx.planned_range() if it > start_from]
    if not todo:
        end_it = int(ctx.plan.get("end_it", 0) or 0)
        if start_from >= end_it:
            # G3（plan/offline-rerun-local-first §4.2）：区分「本机段落已完成」（下一步是清目录/
            # 等新包）与「无事可做」。前者原来是同一个词 —— 人看不出该动哪一步。
            ctx.log(
                f"本机段落已完成（it{start_from} ≥ end_it{end_it}）——没有要跑的轮次；"
                f"要用新段请清空 {ctx.store.root} 或等新包（或置 CFG.force_pack=true）"
            )
        else:
            ctx.log("计划内的轮次都已在产物里——无事可做")
        ctx.store.finalize(state="complete", summary={"last_it": start_from, "rows": len(ctx.store.rows)})
        # 无事可做也可能**有东西要补传**：上次会话断网、这次连上了，积压全在这一步补完。
        ctx.deliver_final(it_end=start_from, state="noop", summary={"rows": len(ctx.store.rows)})
        ctx.close_delivery()
        _close_eval(ctx)
        return _combined(ctx, last_it=start_from, session=session, state="noop")
    ctx.log(f"自主段开始：{len(todo)} 轮待跑（it{todo[0]} → it{todo[-1]}）")
    # rollout 并行度**在这里**报一次（每段一次，不是每轮）：它是本机规模派生的，而计划里
    # 钉着的是**导出机**的值——两者不同时说出来，否则「明明改大了并发却不见快」没法归因。
    pinned = int(ctx.plan.get("workers", 0) or 0)
    ctx.log(
        f"rollout 并发局数：{ctx.rollout_workers} 局"
        + (f"（计划里钉着 {pinned}——那是导出机的规模，已按本机核数覆盖）" if pinned and pinned != ctx.rollout_workers else "")
        + "；与云机评估同一口径 max(cores−4, cores×0.8)"
    )
    prev = start_from
    stopped = "complete"
    try:
        for it in todo:
            if ctx.budget_left() <= 0:
                stopped = "budget"
                ctx.log(
                    f"预算用尽（{ctx.budget_sec:.0f}s）——在 it{it} **开始之前**干净停机"
                    "（产物自洽，可续跑）"
                )
                break
            t0 = time.time()
            result = _run_with_retries(ctx, it, prev_it=prev)
            session.append(_checkpoint(ctx, it, result, wall_sec=round(time.time() - t0, 2)))
            _maybe_cloud_eval(ctx, it)
            prev = it
    except (ProtocolError, RetryableError) as e:
        fail_summary = {"last_it": prev, "error": f"{type(e).__name__}: {e}"}
        ctx.store.finalize(state="failed", summary=fail_summary)
        ctx.log(f"自主段在 it{prev + 1} 处失败（{type(e).__name__}: {e}）——产物已收尾，可续跑")
        # 段失败也要报：控制面最需要的就是「这条腿停了、停在哪、为什么」——云机那一侧
        # 的日志人看不到，而失败是**最该被看见**的状态。
        ctx.deliver_final(it_end=prev, state="failed", summary=fail_summary)
        ctx.close_delivery(timeout=min(DRAIN_FLUSH_SEC, 30.0))  # 失败收尾少等：先让人看到失败
        # 失败也要收评估：已评的局是「这条腿到底怎么样」的一手证据（比失败本身更有用）
        _close_eval(ctx, timeout=min(runner_timeout(ctx), 120.0))
        raise
    # 收尾顺序刻意是「评估 → 产物打包 → 补传摘要」：finalize 打的 artifacts.zip 要**包含**
    # 刚收完线的评估账本（eval_log.jsonl），而摘要里报的行数/末轮才与包一致。
    _close_eval(ctx)
    summary = {"last_it": prev, "rows": len(ctx.store.rows), "session": len(session)}
    ctx.store.finalize(state="complete" if stopped == "complete" else stopped, summary=summary)
    ctx.deliver_final(it_end=prev, state=stopped, summary=summary)
    ctx.close_delivery()
    return _combined(ctx, last_it=prev, session=session, state=stopped)


def _run_with_retries(ctx: RunContext, it: int, *, prev_it: int) -> dict:
    """单轮 + 有限重试（自主模式没有 hub 兜底：瞬时失败重试，确定性失败直接上抛）。"""
    last: BaseException | None = None
    for attempt in range(1, ITER_RETRIES + 2):
        try:
            return _run_iteration(ctx, it, prev_it=prev_it)
        except RetryableError as e:  # 瞬时（单局超时 / rc≠0 / 传输）——重试值得
            last = e
            ctx.log(f"it{it} 第 {attempt} 次失败（瞬时）：{e}")
    assert last is not None
    raise last


def _combined(ctx: RunContext, *, last_it: int, session: list[dict], state: str) -> dict:
    """合并结果：末轮形状（直接走 hub 既有落位链）+ `iters` 明细 + `artifacts` 元信息。

    为什么保持「末轮形状」而不是发明新结构：hub 侧 `verify_and_land` 逐字段对的是**本
    job 自己**的 data_fp / init_weights_fp / commit_echo，那三个字段这里原样保留 ⇒ 半离
    线段落地**零新代码**。逐轮明细是 additive 的（旧读方忽略未知键）。
    """
    manifest = ctx.manifest
    # 账本一行 = 一轮训练（起点快照不记账，所以这里不需要任何过滤）：`iters` 的形状
    # 校验交给协议层（`validate_result` 要求逐轮 agg + 严格递增 it），这里只负责
    # 「一轮都没有」这个更早的判据——拿空 iters 当成功结果会污染 hub 账本。
    rows = sorted(ctx.store.rows, key=lambda r: int(r["it"]))
    if not rows:
        raise ProtocolError(
            "自主段没有任何一轮的采集报告可回传（iters 为空）——这不是一个成功的结果"
            f"（产物目录 {ctx.store.root}）"
        )
    last_row = rows[-1]
    last_it = int(last_row["it"])
    result: dict = {
        "job_id": manifest["job_id"],
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(ctx.store.weights_path(last_it).read_bytes()),
        "opt_tar_b64": _encode_opt(ctx.store.opt_path(last_it)),
        "agg": dict(last_row.get("agg", {}) or {}),
        "report": dict(last_row.get("report", {}) or {}),
        "commit_echo": manifest["commit"],
        "ppo_sec": float(sum(float(r.get("ppo_sec", 0.0) or 0.0) for r in session)),
        "iters": [
            {
                "it": int(r["it"]),
                "agg": dict(r.get("agg", {}) or {}),
                "report": dict(r.get("report", {}) or {}),
                "wall_sec": float(r.get("wall_sec", 0.0) or 0.0),
                "weights_fp": str(r.get("weights_fp", "")),
            }
            for r in rows
        ],
        "it_end": last_it,
        "run_state": str(state),
        "plan_sha256": ctx.plan_sha256,
        "artifacts": {
            "dir": str(ctx.store.root),
            "zip": str(ctx.store.root / ArtifactStore.ALL_ZIP),
            "rows": len(ctx.store.rows),
            "start_it": int(manifest["it"]),
        },
    }
    return result


def _encode_opt(opt_path: Path) -> str:
    return encode_opt_tar(opt_path.read_bytes()) if opt_path.exists() else ""

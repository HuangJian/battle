"""remote/plan_handoff.py — 半离线段的**交接面**（S5 第十三刀，2026-09-27）。

从 `remote/plan_run.py` 逐字节搬来的一整块（562 行，4 个跨度）：**计划校验 → 取包播种 → 上下文 →
评估装配**。hub 在交接时给一次（课程 + 初始权重 + 代码 + `plan.json`），这一块把那个包变成可跑的
`RunContext`；引擎（`remote.plan_run`，留守）拿到 ctx 之后只管「怎么跑」。

* **计划校验** —— `verify_plan_file`（sha / 形状 / pairs_fp 三道门；worker 在 kind=run 的尾巴上调用）。
* **取包播种** —— `open_run_context` + 9 个助手（demo/ref blob / opt 字节 / TS 运行时树 / 起始 checkpoint）。
* **上下文** —— `RunContext`（交接面与引擎之间的**唯一接口**；含 `deliver_*` 三薄委托）。
* **评估装配** —— `_setup_cloud_eval` / `_eval_job_builder` / `_eval_round_done`：后台评估腿的**起始
  半边**；驱动/收线半边（`_maybe_cloud_eval` / `_close_eval`）留守引擎——两半只通过 `ctx.eval_*` 槽位耦合。

## 槽位契约（`ctx.eval_*`：谁写谁读）

本模块**写**：`eval_on_cloud` / `eval_plan` / `eval_slots` / `eval_runner`（`_setup_cloud_eval` 一处
写入；失败路径只写 `eval_on_cloud=False`），并在 `_eval_job_builder` 里**读** `eval_game_timeout_sec`。
引擎（`_maybe_cloud_eval` / `runner_timeout` / `_close_eval`）**读**前四个槽并驱动收线。
改任一槽的名字/含义 = 同时改两模块（守卫 `tests/remote/test_plan_handoff_split.py` 钉住写入面）。

## 依赖方向

`plan_handoff → {common.logutil, common.protocol, common.platform_utils, remote.artifacts, remote.bundle,
remote.offline_deliver, biz.plan}`（全向下，DAG）。**零**引用 `remote.plan_run` / `remote.worker` /
`remote.run_loop`——反向由 `plan_run` 的门面承接（`X as X`，历史 import 路径一行不改）。

## 注入点（patch 纪律）

本模块读的模块全局（`EVAL_ALTERNATE_WAIT_SEC` / `time` / `TS_TREE_DIR` …）解析在**本模块命名空间**：
要拦交接面请 patch `remote.plan_handoff.*`（patch 门面 = 改副本，不生效）。注意
`EVAL_ALTERNATE_WAIT_SEC` 有**双读者**：`_setup_cloud_eval`（本模块）与 `_maybe_cloud_eval`（引擎）
各读各家的绑定——patch 一处不影响另一处（守卫钉住这条事实）。
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
from common.platform_utils import cpu_worker_slots
from common.protocol import (
    BLOB_DEMO,
    BLOB_REF,
    EVAL_SCRIPT,
    PLAN_NAME,
    ProtocolError,
    blob_path,
    decode_opt_tar,
)
from remote.artifacts import (
    ArtifactStore,
    resolve_artifact_dir,
    sha256_bytes,
    sha256_file,
)
from remote.bundle import CODE_NAME as BUNDLE_CODE_NAME
from remote.offline_deliver import (
    DRAIN_FLUSH_SEC,
    OfflineDeliverer,
    make_deliverer,
)
from worker.plan import plan_pairs_fp, planned_iters, validate_plan

#: 产物目录里随段携带的 TS 运行时树（让「只下载产物 zip」的机器也能续跑）。
TS_TREE_DIR = "ts_code"
#: 任务包（全离线）里随包携带的两份运行时字节：python 代码快照与 TS 运行时 zip。
#: 有它们，节点可以**完全不联网**跑完整段（`run_job` 的 preloaded 直接吃这两份字节）。
TS_CODE_ZIP_NAME = "ts_code.zip"

#: 一轮评估**提交后**等它收线的时限（秒）——「交替」就是在这里做出来的。
#:
#: 为什么必须等（2026-09-25 云机卡死取证）：`_maybe_cloud_eval` 在 checkpoint 之后提交评估，
#: 而**下一轮的第一步就是 rollout**（`_run_with_retries` → `run_job` → `run_iter_rollout`），
#: 两条腿因此是**同时**各开满一份（96 核配额上 220+220；而那个 220 本身就是把宿主机报的
#: 224 核当成配额的产物，见 `common.platform_utils.effective_cores`）—— 旧注释那句「rollout 与 eval
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
        #: `common.platform_utils.cpu_worker_slots()`（用户 2026-09-22 立口径；2026-10-03 用户校准
        #: 预留 4 → 2：「两者都使用 max(cores−2, cores×0.8)」）。「交替」由
        #: `EVAL_ALTERNATE_WAIT_SEC` 那条有界等保证（见它）。
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

    def deliver_final(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
    ) -> None:
        """段末：补完积压 + 推一份摘要（跑到哪 / 什么状态 / 为什么停）。

        后台模式下同样只是入队，由 `close_delivery()` 做**有界** flush。
        `end_it_reached`（T6）：本段是否跑到计划终点（判据在驱动侧 `plan_run._end_it_reached`）。
        """
        if self.deliverer is None:
            return
        self.deliverer.submit_final(
            it_end=int(it_end),
            state=str(state),
            summary=summary,
            end_it_reached=end_it_reached,
        )

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
    _seed_ref_blob_cache(
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


def _seed_ref_blob_cache(
    *,
    manifest: dict,
    job_dir: str | Path,
    work_dir: str | Path,
    artifacts_root: str | Path | None,
    log: Callable[[str], None] = _log_default,
) -> None:
    """run 启动期 ref 权重种子（§363 kickstart BC 锚）：与 `_seed_demo_blob_cache` 同规——
    manifest.ref_sha 非空时，把 ref 原始字节落进 `work_dir/blob_cache/<sha>`（内容寻址键 =
    sha256(raw) = ref_weights_fp），此后逐轮缓存命中、零传输。

    字节来源（按序）：产物目录 `ref_weights.json`（bundle 导入布局）→ job 目录 `blob.ref`。
    任一命中但 sha 不符 = 跳过找下一个；全无 ⇒ 启动期**响亮拒绝**（不等跑到 it1 的 PPO
    装载才炸）。run 模式没有可用的 hub blob 通道（逐轮 manifest 是节点本地合成的，hub 上
    无此 job），所以这里**不**尝试联网下载——离线腿走 bundle（自动带包），手工递送请预置
    文件或缓存。

    ★ 2026-10-03 现场（x20-adv-hurt 首跑）：bundle 带了 code/ts/init 却没带 ref ⇒ 云端
    `plan_run._run_iteration` 用 `run_job("", "", …)` 合成轮次，`_resolve_blob` 未命中后向
    **空 base_url** 发 GET：`ValueError: unknown url type` ×3 重试耗尽，it1 处整段失败。
    """
    sha = str(manifest.get("ref_sha", "") or "")
    if not sha:
        return
    cache = Path(work_dir) / "blob_cache"
    dst = cache / sha
    if dst.is_file():
        try:
            if sha256_file(dst) == sha:
                log(f"ref 权重缓存命中（{sha[:12]}…）——零传输")
                return
            log("ref 权重缓存损坏（sha 不符）——重新种子")
        except OSError:
            pass
    cands: list[Path] = []
    if artifacts_root is not None:
        cands.append(Path(artifacts_root) / "ref_weights.json")
    cands.append(blob_path(job_dir, BLOB_REF))
    for c in cands:
        try:
            raw = c.read_bytes()
        except OSError:
            continue
        if sha256_bytes(raw) != sha:
            log(f"ref 候选 {c} sha 不符——跳过")
            continue
        cache.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(dst)
        log(f"ref 权重已种子进 blob_cache（{len(raw) / 1e6:.1f}MB，{sha[:12]}…）")
        return
    raise ProtocolError(
        f"manifest 要 kickstart ref（sha={sha[:12]}…）但节点侧无字节：产物目录缺 "
        "ref_weights.json、job 目录缺 blob.ref、blob_cache 未命中——修法：用带 ref 的任务包"
        "重导（export_bundle 见 job 目录 blob.ref 即自动打包），或把 ref_weights.json 放进产物目录"
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

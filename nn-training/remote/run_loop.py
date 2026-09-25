"""remote/run_loop.py —— 半离线自主段执行器（kind=run）。

**语义**：hub 在交接时给一次（课程 + 初始权重 + 代码 + `plan.json`），此后**云机自主**
把计划里的轮次跑完（rollout + PPO 全在节点），逐轮把权重/opt/指标写进本地产物目录
（`remote/artifacts.py`），最后把「末轮形状」的结果回传（能通则通，不通也不影响产物）。

**为什么整段自主要做成「同一个 run_job 递归 N 次」而不是另写一条训练链**：一次迭代的
全部知识（D14 血缘、课程/奖励重建、opt 解析、PPO load→chunk→update、产物打包、
`validate_result` 自查）都在 `worker.run_job` 里，且**只有那一份**。本模块只做三件
hub 本来在做的事——「下一轮跑哪些局」「下一轮的 manifest 长什么样」「权重怎么传下去」
——每轮都合成一个与 kind=iter **逐字段同构**的 job 再喂回 `run_job`。于是离线轮与
hub 监管轮走的是字面同一条代码路径，连校验口径都一致。

链条的三个衔接点（也是本模块的全部内容）：

  1. **对集**：`rl.plan.pairs_for`（`build_pairs` 纯函数重放，计划是唯一输入）；
  2. **argv**：`rl.plan.iter_spec`（模板按 flag 重定向，不复制 `build_rollout_cmd`）；
  3. **权重与 opt**：上一轮结果里的 `weights_json` → 下一轮 payload 的 `init_weights.json`
     （`init_weights_fp` = 该文件 sha = `wver`，与 hub 链路同义）；上一轮 `opt_tar_b64`
     落进 blob_cache → 下一轮 manifest 带 `opt_sha` 命中缓存（Adam 动量不丢，D5）。

产物（`remote/artifacts.py`）是**唯一**长期记录：hub 在不在都不影响它；`ts_code/` 也随
产物携带（几 MB），因此「只下载产物 zip」的机器可以独立续跑。

**产物补传**（`remote/offline_deliver.py`；可选）：每轮落盘之后顺手问一句 hub 在不在，
在就把积压的轮次 best-effort 推上去（`delivered.json` 记账，重启后续投）。给 `hub_url`
+ token 即开启；不给、或探不到、或鉴权被拒 ⇒ 零影响（安静跳过，训练照跑）。**补传永
不阻塞、永不抛**：它只是「产物比别人早一步到达控制面」的旁路，不是训练的一部分。

命令行（新会话续跑 / 无 hub 的纯手工递送 / 中途恢复在线回传）：

    python -m remote.run_loop --artifacts <产物目录> [--budget-sec 3600] [--device cuda]
                                     [--hub-url https://hub.example --hub-token-file ~/tok]

手工递送（完全没有 hub）时，目录里放四样东西即可开跑：`plan.json` + `manifest.json`
（课程/超参快照）+ `init_weights.json`（起点权重）+ `ts_code/`（TS 运行时树）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

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
from remote.protocol import (
    BLOB_DEMO,
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
from remote.protocol import (
    job_id as make_job_id,
)
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
    print(f"[{time.strftime('%H:%M:%S')}] [run] {msg}", flush=True)


def _real_run_job(*args: Any, **kw: Any) -> dict:
    """延迟导入 `worker.run_job`（worker 依赖 torch 链，本模块顶层保持轻）。"""
    from remote.worker import run_job

    return run_job(*args, **kw)


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
    所以这里**不**尝试联网下载——离线腿走 bundle（自动带包），半离线请预置文件或缓存。
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
    云机评估最终要的是「有 `tools/sim/export-eval-game.ts` 的那个目录」，所以宁可在这里
    多探一层，也不把「相对路径 + bun 在 PATH」这条前提留在调用点靠猜。
    """
    if cache_dir is None:
        return None
    cache = Path(cache_dir)
    sha = str(manifest.get("ts_code_sha256", "") or "")
    cand = cache / sha if sha else None
    for p in (cand, cache):
        if p is not None and (Path(p) / "tools" / "sim" / "export-eval-game.ts").exists():
            return Path(p)
    return cand if (cand is not None and Path(cand).is_dir()) else None


def load_planned_manifest(path: str | Path) -> tuple[dict, dict]:
    """从产物目录读回 (plan, manifest)——standalone 续跑的入口（不需要 hub）。"""
    root = Path(path)
    plan_p = root / ArtifactStore.PLAN_NAME
    man_p = root / ArtifactStore.MANIFEST_NAME
    if not plan_p.exists() or not man_p.exists():
        raise ProtocolError(
            f"产物目录不完整：缺 {plan_p.name} / {man_p.name} 之一（不能续跑）: {root}"
        )
    plan = validate_plan(json.loads(plan_p.read_text(encoding="utf-8")))
    manifest = normalize_manifest(json.loads(man_p.read_text(encoding="utf-8")))
    return plan, manifest


def ensure_ts_cache_layout(root: Path, *, ts_tree: Path | None, sha: str) -> Path | None:
    """把产物里的 TS 树布置成 worker 认的 **cache 布局**（`<root>/<sha>/`）。

    worker 的 `_ensure_ts_code` 只认内容寻址的 cache 目录；产物里带的是普通树（拷一次，
    比每轮重建便宜）。已有布局则原样复用（**幂等**：同一目录反复续跑不重复拷贝）。
    """
    if not sha:
        return None
    cache_root = root / "ts_code_cache"
    cache = cache_root / sha
    src = Path(ts_tree) if ts_tree is not None else root / TS_TREE_DIR
    if cache.is_dir():
        return cache_root
    if not Path(src).is_dir():
        return None
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, cache, dirs_exist_ok=True)
    except OSError:
        return None
    return cache_root


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
    run_job = ctx.run_job_fn or _real_run_job
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
            # **原样传 0（= 没指定）**：由 `run_cloud_eval` 按 `remote/game_watch.py` 解析成
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
    #: 产物补传：本 job 就是从这条连接上领来的，地址与 token 手边就有——半离线段因此
    #: **默认就开着补传**（hub 中途失联时不至于「跑完一整段、控制面一无所知」）。
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
    """kind=run 的尾巴：本 job 自己那一轮已经跑完（`first_result`），接着把计划跑完。

    返回**合并结果**（末轮形状 + `iters` 明细 + `it_end` + `artifacts`），它可以照原样
    走 hub 的既有落位链（`verify_and_land`：data_fp / init_weights_fp / commit_echo 都是
    本 job 自己的，逐字段对得上）——半离线段在 hub 侧**不需要**新代码。

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


def run_standalone(
    *,
    artifacts_dir: str | Path,
    plan: dict | None = None,
    manifest: dict | None = None,
    work_dir: str | Path | None = None,
    ts_code_dir: str | Path | None = None,
    code_cache_dir: Path | None = None,
    device: Any = "cuda",
    torch_threads: int = 0,
    max_iters: int = 0,
    budget_sec: float = 0.0,
    #: 云机 A 层评估（`eval_on_cloud`）：每 `eval_every` 轮在本机跑 A 层语料。
    eval_on_cloud: bool = False,
    eval_slots: int = 0,
    eval_game_timeout_sec: float = 0.0,
    #: rollout 并行局数（0 = 按本机核数自动；与 eval 同一口径，见 `RunContext.rollout_workers`）。
    rollout_workers: int = 0,
    #: hub 给的续跑锚点目录（`--resume-dir`；见 `apply_resume_overlay`）。
    resume_dir: str | Path | None = None,
    #: 产物补传（可选）：给了 hub 地址 + token 就会在每轮落盘后尽力推一份上去。
    #: 全离线腿的常见形态是「一开始连不上（甚至 hub 关机）、后来能连上了」——
    #: 这里不做任何"记住连不上就别试"的记忆，每轮都探一次（探不到就一跳而过）。
    hub_url: str = "",
    hub_token: str = "",
    #: 本份产物在 hub 里的归位键（见 `OfflineDeliverer.course`；空 = 单课程 hub）。
    hub_course: str = "",
    deliver: bool = True,
    run_job_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] = _log_default,
) -> dict:
    """**无 hub 续跑**：产物目录是唯一输入（新会话 / 换了台机器都走这条）。

    接续点是 `state.json.last_it`（其权重/opt 已在目录里）；`plan`/`manifest` 缺省从目录
    读回——所以命令行的最小形态就是 `python -m remote.run_loop --artifacts <dir>`。
    """
    root = Path(artifacts_dir)
    if plan is None or manifest is None:
        plan, manifest = load_planned_manifest(root)
    # ★ 2026-09-22 事故修复：**加载课程上下文**。此前 `run_standalone` 从不传 course ⇒
    # `iter_spec` 里 `stage_json_of(course, stage)` 拿不到自定义关（ladder 2000+）的
    # stageJson ⇒ `retarget_argv` 把计划里的 `--stage-json` **整对删掉** ⇒ 导出器解析
    # stage 2000 失败 → 静默空局（0 samples、rc=0、零 shard）→「没有任何 shard」误报
    # 成环境问题。与交互 worker 同款加载（`rl.config.load_course`，快照即来源）。
    course_path = root / "course.jsonc"
    course = None
    if course_path.exists():
        try:
            from rl.config import load_course

            course = load_course(str(course_path))
        except Exception as e:
            log(f"WARN: 课程快照加载失败（自定义关 stage 将不可解析，rollout 会出空局）: {e}")
    wd = Path(work_dir) if work_dir is not None else root / "work"
    sha = str(manifest.get("ts_code_sha256", "") or "")
    ts_root = ensure_ts_cache_layout(
        root,
        ts_tree=Path(ts_code_dir) if ts_code_dir is not None else None,
        sha=sha,
    )
    ctx = open_run_context(
        plan=plan,
        plan_sha256=sha256_bytes((root / ArtifactStore.PLAN_NAME).read_bytes()),
        manifest=manifest,
        job_dir=root,
        work_dir=wd,
        artifacts_dir=root,
        device=device,
        torch_threads=torch_threads,
        code_cache_dir=code_cache_dir,
        ts_code_cache_dir=ts_root,
        ts_tree=Path(ts_code_dir) if ts_code_dir is not None else root / TS_TREE_DIR,
        course=course,
        max_iters=max_iters,
        budget_sec=budget_sec,
        eval_on_cloud=eval_on_cloud,
        eval_slots=eval_slots,
        eval_game_timeout_sec=eval_game_timeout_sec,
        rollout_workers=rollout_workers,
        hub_url=hub_url,
        hub_token=hub_token,
        hub_course=hub_course,
        deliver=deliver,
        run_job_fn=run_job_fn,
        log=log,
    )
    if ts_root is None:
        log(
            "WARN: 产物目录里没有 TS 运行时树（ts_code/）且未给 --ts-root——"
            "rollout 需要它；请把上一段的 ts_code/ 一起带过来"
        )
    _require_offline_runtime(ctx, work_dir=wd)
    # hub 给的续跑锚点（若任务包比 hub 上的最新进度旧）：**在起点读取之前**采纳，
    # 于是下面 start_from 自然从锚点那一轮之后接上。
    apply_resume_overlay(ctx, resume_dir)
    st = ctx.store.read_state() or {}
    start_from = int(st.get("last_it", plan["start_it"]))
    log(f"续跑起点 it{start_from}（计划 it{plan['start_it']} → it{plan['end_it']}）")
    return _drive(ctx, session=[], start_from=start_from)


def apply_resume_overlay(ctx: RunContext, resume_dir: str | Path | None) -> int:
    """把 hub 递回来的**续跑锚点**（权重 + opt + 指标行）铺进产物目录，返回采纳的 it。

    为什么锚点不由包自带：任务包是导出那一刻的只读快照，而云机的中断与重领发生在
    它**之后**——重领时 hub 手上已经有更新的（自回传的或人工导入的）完整轮次。
    只认「同轮齐全」的那一轮（weights + opt + row 三件都在，由 hub 侧选定）：缺 opt 的轮
    存在只是「Adam 动量归零」的小代价，但把它当成续跑锚点会与「同轮齐全」的语义不符
    （用户 2026-09-22 口径：必须同轮齐全，否则退到更早轮）。

    守卫（任一不满足就**忽略锚点并响亮记一笔**，绝不因此让整段跑不起来）：
      * `resume.json` 里权重指纹与实际字节相符（传输损坏不得进产物目录）；
      * 锚点轮比产物当前的 `last_it` **更新**（更旧/同轮 = 没东西可接）；
      * 同轮文件已存在且指纹相同时视为已采纳（幂等：会话重启再领不会重复记账）。
    """
    if not resume_dir:
        return 0
    d = Path(resume_dir)
    meta_p = d / "resume.json"
    if not meta_p.is_file():
        ctx.log(f"续跑锚点目录里没有 resume.json（{d}）——忽略")
        return 0
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        it = int(meta["it"])
        want_fp = str(meta.get("weights_fp", "") or "")
    except (OSError, ValueError, KeyError, TypeError) as e:
        ctx.log(f"续跑锚点元信息不可读（{type(e).__name__}: {e}）——忽略")
        return 0
    src_w = d / f"{ArtifactStore.IT_PREFIX}{it:03d}" / "weights.json"
    src_opt = d / f"{ArtifactStore.IT_PREFIX}{it:03d}" / "opt.tar"
    src_row = d / f"{ArtifactStore.IT_PREFIX}{it:03d}" / "row.json"
    if not src_w.is_file():
        ctx.log(f"续跑锚点 it{it} 缺权重文件（{src_w}）——忽略")
        return 0
    got_fp = sha256_file(src_w)
    if want_fp and got_fp != want_fp:
        ctx.log(
            f"续跑锚点 it{it} 权重指纹不符（声明 {want_fp[:16]}… 实得 {got_fp[:16]}…）"
            "——忽略（传输损坏）"
        )
        return 0
    st = ctx.store.read_state() or {}
    cur = int(st.get("last_it", ctx.plan["start_it"]))
    same = ctx.store.weights_path(it).is_file() and sha256_file(ctx.store.weights_path(it)) == got_fp
    if cur >= it:
        # `same` 时就是幂等重入（会话重启又领到同一个锚点）——不是异常，安静跳过。
        ctx.log(
            f"续跑锚点 it{it} 不新于产物当前进度 it{cur}"
            + ("（同一轮，已在产物里）" if same else "（更旧）")
            + "——忽略"
            + ("" if same else "；若这不是预期，检查 hub 侧锚点与产物目录是否同一课程")
        )
        return 0
    if same and src_opt.is_file():
        ctx.log(f"续跑锚点 it{it} 的权重已在产物里（指纹相同）——只补状态")
    row: dict | None = None
    if src_row.is_file():
        try:
            loaded = json.loads(src_row.read_text(encoding="utf-8"))
            row = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            row = None
    opt_raw = b""
    if src_opt.is_file():
        opt_raw = src_opt.read_bytes()
    if not opt_raw:
        ctx.log(f"WARN: 续跑锚点 it{it} 没有 opt.tar——Adam 动量从头（其余照常）")
    ctx.store.checkpoint(it, weights_json=src_w.read_bytes(), opt_tar=opt_raw, row=row)
    ctx.last_opt_sha = sha256_bytes(opt_raw) if opt_raw else ""
    ctx.log(
        f"续跑锚点已采纳：it{it}（{meta.get('source', '?')}，权重 {got_fp[:12]}…，"
        f"opt {len(opt_raw)} 字节）——本段从 it{it} 之后继续"
    )
    return it


def _require_offline_runtime(ctx: RunContext, *, work_dir: Path) -> None:
    """standalone（无 hub）入口的硬门：没有代码快照就**现在**响亮拒收。

    为什么不在 `_run_iteration` 里卡：半离线轮（hub 发的 kind=run）走的是同一条迭代函数，
    它的代码是 worker 自己那一轮从 hub 下好、已落进内容寻址缓存（`code_cache/<sha>/`）的
    ——那里没有 `code.zip` 字节也**没问题**。而 standalone 是「无 hub」入口：既没有缓存、
    又没随包字节时，`run_job` 会拿着空 base_url 去下载，报一个跟真因无关的错（重试/联网
    都治不了）。所以卡在入口，并把修法写进错误里。
    """
    if ctx.code_zip_bytes:
        return
    sha = str(ctx.manifest.get("code_sha256", "") or "")
    root = ctx.code_cache_dir if ctx.code_cache_dir is not None else work_dir / "code_cache"
    if sha and (Path(root) / sha).is_dir():
        return  # 缓存已命中（同一台机器上先跑过一段 / 共享缓存）
    raise ProtocolError(
        "无 hub 运行需要代码快照，但没有：产物目录里没有 code.zip，也没有 code_cache/"
        f"{sha[:12] if sha else '<sha>'}…。整段重用同一个 commit 的代码，节点没有仓库可回退。"
        "修法：用任务包（`python -m remote.bundle import <zip> --dest <目录>`）或把 code.zip"
        "放进产物目录（`--code-cache-dir` 指向已有缓存也可）"
    )


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


# ------------------------------------------------------------------ CLI（无 hub 续跑）


def _resolve_hub_token(inline: str, token_file: str) -> str:
    """token 解析（flag > 文件 > `$BATTLE_HUB_TOKEN`）。

    为什么支持环境变量：Kaggle/Colab 里凭据的自然容器是 secret / 环境变量（而不是命令行
    ——命令行会进 notebook 输出与 shell 历史）。**token 不进任务包**（包会四处搬运）。
    """
    if inline:
        return inline.strip()
    if token_file:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[run] 读 --hub-token-file 失败：{e}", file=sys.stderr, flush=True)
            return ""
    import os

    return (os.environ.get("BATTLE_HUB_TOKEN") or "").strip()


def main(argv: list[str] | None = None) -> int:
    """`python -m remote.run_loop --artifacts <dir>`：产物目录即任务，续跑到计划末尾。"""
    ap = argparse.ArgumentParser(description="自主段运行（产物目录是唯一输入；不需要 hub）")
    ap.add_argument(
        "--bundle",
        default="",
        help="全离线任务包 zip（remote/bundle：先铺成产物目录再跑；与 --artifacts 二选一）",
    )
    ap.add_argument(
        "--artifacts",
        default="",
        help="产物目录（含 plan.json/state.json；用 --bundle 时可作铺设目标）",
    )
    ap.add_argument("--work-dir", default="", help="临时工作目录（缺省 <artifacts>/work）")
    ap.add_argument("--ts-root", default="", help="TS 运行时树（缺省 <artifacts>/ts_code）")
    ap.add_argument("--device", default="cuda", help="torch device: cpu / cuda / cuda-dp / tpu")
    ap.add_argument("--threads", type=int, default=0, help="torch intra-op threads（0=默认）")
    ap.add_argument("--max-iters", type=int, default=0, help="本次最多再跑几轮（0=到计划末尾）")
    ap.add_argument(
        "--budget-sec",
        type=float,
        default=0.0,
        help="本次最多跑多少秒（0=不限）——Kaggle 会话到点前干净停机的把手",
    )
    ap.add_argument(
        "--hub-url",
        default="",
        help="hub 地址（给了它就开启产物补传；缺省取任务包 task.json 里的 hub_url）",
    )
    ap.add_argument("--hub-token", default="", help="hub Bearer token（或 --hub-token-file / $BATTLE_HUB_TOKEN）")
    ap.add_argument(
        "--hub-course",
        default="",
        help=(
            "本份产物在 hub 里归哪门课（多课程 hub 的补传归位键 = 控制台里那门课的名字）；"
            "空 = 单课程 hub。缺了它，多课程 hub 的补传会以「无法归属课程」被拒"
        ),
    )
    ap.add_argument(
        "--hub-token-file",
        default="",
        help="从文件读 token（Kaggle secret / Colab 挂载；避免进 shell 历史）",
    )
    ap.add_argument(
        "--no-deliver",
        action="store_true",
        help="显式关掉产物补传（纯离线：不探 hub、不推任何字节）",
    )
    ap.add_argument(
        "--eval-on-cloud",
        action="store_true",
        help="在云机跑 A 层评估（同 in-loop 语料/行口径，每 eval_every 轮）；结果落产物目录 eval_log.jsonl",
    )
    ap.add_argument(
        "--eval-slots",
        type=int,
        default=0,
        help="云机评估的并发局数（0 = max(CPU−4, CPU×0.8)，与 rollout 同一口径）",
    )
    ap.add_argument(
        "--rollout-workers",
        type=int,
        default=0,
        help=(
            "rollout 的并发局数（0 = max(CPU−4, CPU×0.8)，与云机评估同一口径）。"
            "非 0 时覆盖计划里钉着的 workers（那是**导出机**的规模）"
        ),
    )
    ap.add_argument(
        "--eval-game-timeout-sec",
        type=float,
        default=0.0,
        help="云机评估单局超时（0 = 用节点兜底：首次尝试 5s、重试上限 ×4，见 "
        "remote/game_watch.py；>0 时每次尝试都用它）",
    )
    ap.add_argument(
        "--resume-dir",
        default="",
        help="hub 给的续跑锚点目录（含 resume.json + it-NNN/{weights.json,opt.tar,row.json}）",
    )
    args = ap.parse_args(argv)
    if not args.artifacts and not args.bundle:
        print("[run] 需要 --artifacts 或 --bundle 之一", file=sys.stderr, flush=True)
        return 2
    if args.bundle:
        # 全离线入口：包 → 产物目录（逐件对账）→ 就地跑。铺在哪由 `--artifacts` 决定，
        # 缺省铺在包旁边（人一眼能找到产物）。
        from remote.bundle import import_bundle, read_bundle_index

        try:
            idx = read_bundle_index(args.bundle)
            dest = args.artifacts or str(Path(args.bundle).with_suffix(""))
            got = import_bundle(args.bundle, dest)
        except ProtocolError as e:
            print(f"[run] 任务包导入失败：{e}", file=sys.stderr, flush=True)
            return 1
        print(
            f"[run] 任务包已导入：{got['artifacts_dir']}（{idx['run_id']} it{idx['it']} →"
            f" it{idx['end_it']}）",
            flush=True,
        )
        args.artifacts = got["artifacts_dir"]
        if not args.ts_root and got.get("ts_code_tree"):
            args.ts_root = got["ts_code_tree"]
        if not args.hub_url and got.get("hub_url"):
            args.hub_url = str(got["hub_url"])  # 包里记着地址（**不记 token**）
    root = Path(args.artifacts)
    hub_token = _resolve_hub_token(args.hub_token, args.hub_token_file)
    try:
        plan, manifest = load_planned_manifest(root)
        result = run_standalone(
            artifacts_dir=root,
            plan=plan,
            manifest=manifest,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            ts_code_dir=Path(args.ts_root) if args.ts_root else None,
            device=args.device,
            torch_threads=args.threads,
            max_iters=args.max_iters,
            budget_sec=args.budget_sec,
            eval_on_cloud=bool(args.eval_on_cloud),
            eval_slots=int(args.eval_slots),
            eval_game_timeout_sec=float(args.eval_game_timeout_sec),
            rollout_workers=int(args.rollout_workers),
            resume_dir=args.resume_dir or None,
            hub_url=args.hub_url,
            hub_token=hub_token,
            hub_course=args.hub_course,
            deliver=not args.no_deliver,
        )
    except (ProtocolError, RetryableError) as e:
        print(f"[run] 失败：{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return 1
    print(
        f"[run] 完成：it{plan['start_it']} → it{result['it_end']}（{result['run_state']}），"
        f"产物 {result['artifacts']['dir']}（{result['artifacts']['zip']}）",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

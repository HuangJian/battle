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

链条的三个衔接点：① 对集 `biz.plan.pairs_for`；② argv `biz.plan.iter_spec`；③ 权重与 opt
（上一轮结果 → 下一轮 payload/`opt_sha`，Adam 动量不丢）。产物（`remote/artifacts.py`）是**唯一**
长期记录；补传（`remote/offline_deliver.py`）永远 best-effort、不阻塞、不抛。

★ 注入点：本模块里读的模块全局（`iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …）都是
**本模块命名空间**的——测试要拦它们请 patch `remote.plan_run.*`，patch `remote.run_loop.*` 打不着
（`run_loop` 只保留门面转发名，见 `tests/remote/test_plan_run_split.py`）。

★ **计划交接面（校验 / 取包播种 / 上下文 / 评估装配）已于 S5 第十三刀逐字节下沉
`remote/plan_handoff`**：本模块只余驱动引擎 + 18 名 `X as X` 门面；要 patch 交接面的
模块全局请去新家（patch 门面不生效）。
"""


from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.log_bundle import LogBundle
from common.protocol import (
    PAYLOAD_NAME,
    ProtocolError,
    RetryableError,
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
from remote.artifacts import (
    ArtifactStore,
    metrics_row,
    sha256_bytes,
    sha256_file,
)
from remote.offline_deliver import DRAIN_FLUSH_SEC

# ---- 计划交接面（校验 / 取包播种 / 上下文 / 评估装配）已下沉 `remote/plan_handoff.py`（S5 第十三刀，2026-09-27）-----
# 实现搬家、名字留门面：`run_loop` 的 13 个 import 块与全仓调用点一行不改（「名字是契约，位置不是」）。
# 交接面读的模块全局解析在 `remote.plan_handoff.*`——要 patch 请去新家（patch 门面不会生效）。
from remote.plan_handoff import (
    EVAL_ALTERNATE_WAIT_SEC as EVAL_ALTERNATE_WAIT_SEC,
)
from remote.plan_handoff import (
    TS_CODE_ZIP_NAME as TS_CODE_ZIP_NAME,
)
from remote.plan_handoff import (
    TS_TREE_DIR as TS_TREE_DIR,
)
from remote.plan_handoff import (
    RunContext as RunContext,
)
from remote.plan_handoff import (
    _blob_roots as _blob_roots,
)
from remote.plan_handoff import (
    _carry_ts_tree as _carry_ts_tree,
)
from remote.plan_handoff import (
    _eval_job_builder as _eval_job_builder,
)
from remote.plan_handoff import (
    _eval_round_done as _eval_round_done,
)
from remote.plan_handoff import (
    _log_default as _log_default,
)
from remote.plan_handoff import (
    _opt_bytes_from_manifest as _opt_bytes_from_manifest,
)
from remote.plan_handoff import (
    _read_opt_file as _read_opt_file,
)
from remote.plan_handoff import (
    _seed_demo_blob_cache as _seed_demo_blob_cache,
)
from remote.plan_handoff import (
    _seed_start_checkpoint as _seed_start_checkpoint,
)
from remote.plan_handoff import (
    _setup_cloud_eval as _setup_cloud_eval,
)
from remote.plan_handoff import (
    _stored_opt_sha as _stored_opt_sha,
)
from remote.plan_handoff import (
    _ts_tree_root as _ts_tree_root,
)
from remote.plan_handoff import (
    open_run_context as open_run_context,
)
from remote.plan_handoff import (
    verify_plan_file as verify_plan_file,
)
from worker.plan import iter_spec, pairs_for

#: 单轮的瞬时失败重试上限（自主模式没有 hub 兜底：重试够了就干净停下留产物）。
ITER_RETRIES = 2

# ------------------------------------------------------------------ 单轮执行


def with_rollout_workers(spec: dict, workers: int) -> dict:
    """把 rollout 规格的并行局数换成**本机**规模（`workers <= 0` = 原样不动）。

    为什么要换：计划里钉着的 `plan.workers` 是**导出那台机器**的规模（常在 8~16 核的本机导出，
    却要在 96 vCPU 的云机上整段跑），而 rollout 与云机 eval 是交替的 ⇒ 两者用同一个
    `common.platform_utils.cpu_worker_slots()`（用户 2026-09-22）。

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
    `tests/remote/test_run_loop.py` 的 4 组段语义回归（整段逐轮同构 / 锚点续跑不重复记账 /
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


def _end_it_reached(plan: dict, *, state: str, it_end: int) -> bool:
    """本段是否跑到了**计划终点**（T6 的唯一判据；纯函数，`plan/auto-offline-handoff §3.6`）。

    为什么判据在云机这一侧：完成态的生产者是**跑过这段的那一侧**——它同时知道
    `state`（怎么停的）与计划终点（`plan["end_it"]`）。控制台/hub 只能转交，不能猜。

    刻意苛刻的两条：
      * 只有 `complete`（跑空 todo）与 `noop`（接续点已在终点之后）算；
        `budget`/`failed` 都不是跑满 —— 半段自报会把这门课提前封成 completed（U6 反例）；
      * `it_end >= end_it`：`max_iters` 截断后的区间跑完也**不算**跑满（计划终点没到）。
    """
    end_it = int(plan.get("end_it", 0) or 0)
    if end_it <= 0 or state not in ("complete", "noop"):
        return False
    return int(it_end) >= end_it


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
        # `end_it_reached` 用同一判据重算：接续点已在终点之后 ⇒ 重报一次「跑满」（幂等；
        # 上一个会话跑满但没送达的摘要，由这一拍补上）。
        ctx.deliver_final(
            it_end=start_from,
            state="noop",
            summary={"rows": len(ctx.store.rows)},
            end_it_reached=_end_it_reached(ctx.plan, state="noop", it_end=start_from),
        )
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
    # T6：跑满计划区间才自报 `end_it_reached`（预算/失败停下的段不算——它们还要人接管）。
    ctx.deliver_final(
        it_end=prev,
        state=stopped,
        summary=summary,
        end_it_reached=_end_it_reached(ctx.plan, state=stopped, it_end=prev),
    )
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

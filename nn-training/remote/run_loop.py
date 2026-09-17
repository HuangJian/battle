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

命令行（新会话续跑 / 无 hub 的纯手工递送）：

    python -m remote.run_loop --artifacts <产物目录> [--budget-sec 3600] [--device cuda]

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
from pathlib import Path
from typing import Any

from remote.artifacts import (
    ArtifactStore,
    metrics_row,
    resolve_artifact_dir,
    sha256_bytes,
    sha256_file,
)
from remote.bundle import CODE_NAME as BUNDLE_CODE_NAME
from remote.protocol import (
    PAYLOAD_NAME,
    PLAN_NAME,
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
        run_job_fn: Callable[..., dict] | None = None,
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
        self.run_job_fn = run_job_fn
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
        run_job_fn=run_job_fn,
        log=log,
    )
    last = int(state.get("last_it", plan["start_it"]))
    _seed_start_checkpoint(ctx, job_dir=Path(job_dir), start_it=int(plan["start_it"]), last_it=last)
    _carry_ts_tree(ctx, ts_code_cache_dir=ts_code_cache_dir, ts_tree=ts_tree)
    return ctx


def _read_opt_file(root: Path, name: str) -> bytes:
    """产物目录里的可选件字节（缺失 → 空：调用方按在线链路处理或响亮报错）。"""
    p = root / name
    try:
        return p.read_bytes() if p.is_file() else b""
    except OSError:
        return b""


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
    ctx.log(
        f"it{it}: {len(pairs)} 局（{len({s for s, _ in pairs})} 关）wver={init_fp[:12]}…"
        + ("（带 Adam 动量）" if ctx.last_opt_sha else "（无 opt：Adam 从头）")
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
    return entry


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
        max_iters=max_iters,
        budget_sec=budget_sec,
        run_job_fn=run_job_fn,
        log=log,
    )
    if ts_root is None:
        log(
            "WARN: 产物目录里没有 TS 运行时树（ts_code/）且未给 --ts-root——"
            "rollout 需要它；请把上一段的 ts_code/ 一起带过来"
        )
    _require_offline_runtime(ctx, work_dir=wd)
    st = ctx.store.read_state() or {}
    start_from = int(st.get("last_it", plan["start_it"]))
    log(f"续跑起点 it{start_from}（计划 it{plan['start_it']} → it{plan['end_it']}）")
    return _drive(ctx, session=[], start_from=start_from)


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
        ctx.log("计划内的轮次都已在产物里——无事可做")
        ctx.store.finalize(state="complete", summary={"last_it": start_from, "rows": len(ctx.store.rows)})
        return _combined(ctx, last_it=start_from, session=session, state="noop")
    ctx.log(f"自主段开始：{len(todo)} 轮待跑（it{todo[0]} → it{todo[-1]}）")
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
            prev = it
    except (ProtocolError, RetryableError) as e:
        ctx.store.finalize(state="failed", summary={"last_it": prev, "error": f"{type(e).__name__}: {e}"})
        ctx.log(f"自主段在 it{prev + 1} 处失败（{type(e).__name__}: {e}）——产物已收尾，可续跑")
        raise
    ctx.store.finalize(
        state="complete" if stopped == "complete" else stopped,
        summary={"last_it": prev, "rows": len(ctx.store.rows), "session": len(session)},
    )
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
    root = Path(args.artifacts)
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

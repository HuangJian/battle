"""remote/run_loop.py —— 半离线自主段（kind=run）的**入口 / CLI**（2026-09-23 拆分后）。

**执行引擎**（计划交接 + 运行上下文 + 单轮 + 主循环）已下沉到 `remote/plan_run.py`（L2），
本模块只剩三件事：

  1. **CLI**（`python -m remote.run_loop --artifacts <产物目录> […]`）与它的 argv 解析；
  2. **独立续跑入口** `run_standalone`（读产物目录里的 plan/manifest/起点权重 → 跑完整段）；
  3. **门面**：`plan_run` 的公开名在这里转发一份（`X as X`），历史调用方与测试的
     `from remote.run_loop import …` 一行不改。

`_real_run_job`（延迟 import `worker.run_job`）也留在这里——它是**入口侧的注入**：
`run_standalone` 把它塞进 `RunContext.run_job_fn`。引擎自己不认识 `worker`（那正是原来那个
`run_loop ⇄ worker` 延迟环的成因，见 `tests/helpers/remote_dag.py` 的账本注释）。

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.protocol import ProtocolError, RetryableError, normalize_manifest
from remote.artifacts import ArtifactStore, sha256_bytes, sha256_file
from remote.plan_run import (
    TS_TREE_DIR as TS_TREE_DIR,
)

# ---- 门面：引擎的公开名在入口侧转发一份（历史调用方与测试的 import 一行不改）--------
# 注入点口径：这些名字的**实现**在 `remote/plan_run.py`，patch 那里才生效；这里的转发名
# 只为 import 兼容（`tests/test_plan_run_split.py` 把两个方向都钉住）。
from remote.plan_run import (
    RunContext as RunContext,
)
from remote.plan_run import (
    _close_eval as _close_eval,
)
from remote.plan_run import (
    _combined as _combined,
)
from remote.plan_run import (
    _drive as _drive,
)
from remote.plan_run import (
    _log_default as _log_default,
)
from remote.plan_run import (
    _maybe_cloud_eval as _maybe_cloud_eval,
)
from remote.plan_run import (
    _seed_demo_blob_cache as _seed_demo_blob_cache,
)
from remote.plan_run import (
    _setup_cloud_eval as _setup_cloud_eval,
)
from remote.plan_run import (
    open_run_context as open_run_context,
)
from remote.plan_run import (
    run_plan_job as run_plan_job,
)
from remote.plan_run import (
    verify_plan_file as verify_plan_file,
)
from remote.plan_run import (
    with_rollout_workers as with_rollout_workers,
)
from rl.plan import validate_plan


def _real_run_job(*args: Any, **kw: Any) -> dict:
    """延迟导入 `worker.run_job`（worker 依赖 torch 链，本模块顶层保持轻）。"""
    from remote.worker import run_job

    return run_job(*args, **kw)


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
        run_job_fn=run_job_fn or _real_run_job,  # 入口侧的注入：CLI 缺省 = 真 worker
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
        "common/game_watch.py；>0 时每次尝试都用它）",
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

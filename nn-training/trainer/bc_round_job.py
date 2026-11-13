"""bc_round_job —— BC `remote-jobs/` 目录的读/收敛 + 一轮结果落位（G4：隐藏目录 + hub 对账）。

**为什么单独成家**：`trainer/bc_loop.py` 的有效 LOC 已顶到预算（<1000），而这两段
都不是编排本身：`find_round_job` 只回答「这一轮盘上还有没有能认领的 job」，
`prune_remote_jobs` 只按 mtime 收敛旧目录。引擎只留一个薄门面，
`tests/trainer/test_bc_ingest_split.py` 仍能看到 `find_round_job` 定义在 `bc_loop.py`
（名字是契约，位置不是）。

**两条判据，缺一不可**：

1. `.` 开头目录**不参与**扫描（Python 的 `glob("*")` 会匹配点开头目录；`.deleted-*`
   因此会把一个已清场的 job 读成「未收口」）。
2. 认领前问 hub：`unknown` / `closed` ⇒ 允许重发并让调用方记账（该 jid 的 per-jid
   resume 已不可用，新 job 从课程级索引续训）；`unreachable` ⇒ **保守认领**，不重发。
   `done` 归 alive：结果已经在 hub 上，认领后落位；重发只会撞旧 result 永远等不到新结果。

**读面不写账本、不发布**：`find_round_job` / `prune_remote_jobs` 只做只读判定，回调
`on_missing` 由引擎决定记什么。`land_bc_result` 是落位面：只在校验失败时写
`bc_land_rejected`（确定性终局），成功路径仍由引擎写 `job_completed` / 轮完成事件。
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from common.log import log
from remote.hub_client import verify_and_land_bc
from remote.hub_http import JOB_CLOSED, JOB_UNKNOWN, JOB_UNREACHABLE, job_reconcile_state
from trainer.dispatch_queue import REPO_ROOT
from worker.bc_config import BcCourseConfig
from worker.bc_ledger import ROUND_LAND_REJECTED_EVENT, append_ledger

MissingHook = Callable[[str, dict, str], None]


class LandRuntime(Protocol):
    """落位需要的最小运行时面（避免 `bc_round_job` 反向 import `bc_loop`）。"""

    course: BcCourseConfig
    traj: Path
    data_root: Path
    out_weights: str
    jsonl_path: Path
    smoke: bool


def land_bc_result(
    rt: LandRuntime,
    it: int,
    jid: str,
    manifest: dict,
    result: dict,
    *,
    log=log,
) -> None:
    """校验 + 落盘一轮 BC 权重；确定性拒收写账本后终止本课。

    fixed 源的发布根是 `course.corpus.fixed_dir`，不是 `traj/bc-data/<round>`；落位必须
    用同一根 + 同一 shard 命名模式 + D14 豁免，否则永远扫到空集（2026-11-08 事故）。
    校验失败是确定性终局：写 `bc_land_rejected` 后抛 `SystemExit`（serve 层映射 ABORT），
    不再让 `loop_runner` 把它吞成每 30s 一次的 retry。
    """
    round_name = "smoke" if rt.smoke else ""
    fixed_source = rt.course.corpus.source == "fixed"
    if fixed_source:
        fixed_dir = str(rt.course.corpus.fixed_dir or "").strip()
        data_root = Path(fixed_dir) if fixed_dir else rt.data_root
    else:
        data_root = rt.data_root / (round_name or f"it{it}")
    try:
        verify_and_land_bc(
            result,
            manifest,
            traj_dir=str(rt.traj),
            it=it,
            out_weights=rt.out_weights,
            round_name=round_name,
            data_root=data_root,
            fixed_source=fixed_source,
            log=log,
        )
    except Exception as e:
        append_ledger(
            rt.jsonl_path,
            {
                "event": ROUND_LAND_REJECTED_EVENT,
                "it": int(it),
                "jid": jid,
                "reason": f"{type(e).__name__}: {e}"[:500],
                "ts": time.time(),
            },
        )
        raise SystemExit(f"[run_bc] it{it}: 落位拒收（{jid[:12]}）——{e}") from e


def train_tuning_meta(train: Any) -> dict[str, float | int | str]:
    """B2/B3 两个训练旋钮的 manifest 片段（bc_loop 的 LOC 预算：单点在此）。"""
    return {
        "weight_exponent": float(train.weight_exponent),
        "patience": int(train.patience),
        # P2-B（2026-11-08）：可训参数范围；旧课程缺键 ⇒ all
        "train_heads": str(getattr(train, "train_heads", "all")),
    }


def train_tuning_argv(train: Any) -> list[str]:
    """同上，本地 `train/bc.py` 的 CLI 片段（顺序/格式只此一份）。"""
    return [
        "--weight-exponent",
        str(train.weight_exponent),
        "--policy-coef",
        str(train.policy_coef),
        "--train-heads",
        str(getattr(train, "train_heads", "all")),
        "--patience",
        str(train.patience),
    ]


def positive_int_or(value: object, fallback: int) -> int:
    """`value` 是 >=1 的 int 就用它（bool 不算），否则回落 `fallback`。"""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return int(value)
    return int(fallback)


def resolve_bc_init_weights(course: BcCourseConfig) -> Path | None:
    """课程 `train.init_from` → 绝对路径（仓库根相对或绝对）；"" = 从随机起。

    缺失文件响亮 SystemExit（publish 端 HubClientError 也是拒发，但这里先拦，
    错误信息指到课程字段）。返回的路径原样进 payload（init_weights.json），
    其 sha 进幂等键（换起点 = 不同 job）。

    2026-11-12：从 `trainer/bc_loop.py` 搬到发布面（本模块已持有 job 载荷的
    tuning 片段；`bc_loop` 保留同名门面）——LOC 预算，行为零变化。
    """
    raw = str(getattr(course.train, "init_from", "") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / raw
    if not p.is_file():
        raise SystemExit(
            f"[run_bc] train.init_from 指向不存在的权重文件：{raw}（解析为 {p}）——"
            "拒发（先确认路径；相对路径按仓库根解析）"
        )
    return p


def _job_progress_rank(job_dir: Path) -> int:
    """job 目录的「真干过活」证据：2 = 有结果；1 = 有 BC 进度；0 = 空壳。

    为什么读盘而不是问 hub（2026-11-13）：同一 `it` 重发布出两个 job 时，manifest 最新的
    那个通常是**没人领的空 job**，而 hub 的内存租约一重启就没了；但训练产物（resume /
    metrics / epoch 快照 / result）落在共享盘上，是重启后仍然可靠的「这个 job 真跑过」证据。
    """
    if (job_dir / "result" / "result.json").exists():
        return 2
    if (
        (job_dir / "bc-resume.json").exists()
        or (job_dir / "bc-metrics.jsonl").exists()
        or (job_dir / "epoch-weights").is_dir()
    ):
        return 1
    return 0


def find_round_job(
    job_root: Path,
    it: int,
    *,
    completed_jids: set[str] | None = None,
    hub_url: str = "",
    token: str = "",
    on_missing: MissingHook | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[str, dict] | None:
    """盘上找**这一轮已发布、还没收口**且 hub 仍可等的 job → `(jid, manifest)`。"""
    done = completed_jids or set()
    cands: list[tuple[float, str, dict, Path]] = []
    try:
        entries = sorted(
            p
            for p in Path(job_root).glob("*/manifest.json")
            if not p.parent.name.startswith(".")
        )
    except OSError:
        return None
    for mp in entries:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or int(m.get("it", -1)) != int(it):
            continue
        if str(m.get("kind", "")) != "bc":
            continue
        jid = str(m.get("job_id") or mp.parent.name)
        if jid in done:
            continue
        try:
            mt = mp.stat().st_mtime
        except OSError:
            mt = 0.0
        cands.append((mt, jid, m, mp.parent))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if len(cands) > 1:
        others = ", ".join(j for _t, j, _m, _d in cands[1:])
        log(
            f"[run_bc] it{it}: 盘上有 {len(cands)} 个未收口 bc job，按 manifest 时间从新到旧"
            f" 对账（其余不回收，可能仍在云机上跑：{others}）"
        )
    if not hub_url or not token:
        return cands[0][1], cands[0][2]
    alive: list[tuple[str, dict, Path]] = []
    for _mt, jid, m, jd in cands:
        state = job_reconcile_state(hub_url, token, jid)
        if state in (JOB_UNKNOWN, JOB_CLOSED):
            why = "hub 侧不存在" if state == JOB_UNKNOWN else "hub 已终局"
            log(
                f"[run_bc] it{it}: 盘上 job {jid[:12]} {why}（{state}）——不认领，允许重发布"
                "（该 jid 的 per-jid resume 不可用；新 job 从课程级索引续训）"
            )
            if on_missing is not None:
                on_missing(jid, m, state)
            continue
        if state == JOB_UNREACHABLE:
            log(
                f"[run_bc] it{it}: 盘上 job {jid[:12]} 的 hub 状态不可达——保守认领，不重发"
            )
            return jid, m
        alive.append((jid, m, jd))
    if not alive:
        return None
    # ★ 2026-11-13（重启后旧 job 的 epoch/结果没人认）：同一 it 有两个 job 时，按 manifest
    # 最新会选中「刚发布、没人领的空 job」，真正在跑/已回传的那个被永久跳过（控制台零行）。
    # 证据读**本地盘**（hub 与 trainer 共享同一份），不依赖 hub 的内存租约 —— 那个东西
    # hub 一重启就没了，而 `bc-resume.json` / `bc-metrics.jsonl` / `result/` 还在。
    for jid, m, jd in alive:
        if _job_progress_rank(jd) > 0:
            if (jid, m, jd) != alive[0]:
                log(
                    f"[run_bc] it{it}: 盘上 job {jid[:12]} 已有训练进度/结果——优先认领"
                    "（比它新的 job 还是空的；2026-11-13 重启事故）"
                )
            return jid, m
    return alive[0][0], alive[0][1]


def prune_remote_jobs(job_root: Path, keep: int = 3) -> None:
    """remote-jobs 旧 job 目录收敛（按 mtime 保留最近 keep 个；与 worker 侧同策略）。

    `.` 开头目录（`.deleted-*` / `.extra_tmp`）**既不计数也不删除**：它们是清场标记，
    不是 job；把它们算进 keep 会挤掉真正的在飞 job（G4 隐藏目录纪律）。
    """
    if not job_root.exists():
        return
    dirs = sorted(
        (d for d in job_root.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in dirs[keep:]:
        try:
            shutil.rmtree(d)
        except OSError:
            pass

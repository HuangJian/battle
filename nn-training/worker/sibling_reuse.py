"""sibling_reuse —— 直接到**同族课程的工作目录**里取现成的局（plan/episode-reuse.plan.md）。

用户 2026-10-22 口径：**不做缓存**（缓存会让内存/硬盘爆炸）。复用面只限「盘上已经有的东西」：

1. **找出可复用的课程**：`episode_identity_fp` 相同（= 起点权重之外的 level/关卡/难度/
   max_ticks/玩家配置/决策粒度全同；reward 刻意不在其中），且排除自己。它们的 `traj`
   目录就是候选工作目录（课程文件里写的那个，相对 `nn-training/` 或仓库根解析）。
2. **到工作目录里找这一局**：
   · rollout ⇒ `**/rl_s{stage}_seed{seed}/manifest.json` 且 `wver` 与本次**逐字节同**
     （`paired_rotate_seed` 不必单独比：抽到的 seed 已经写在这局的目录名/argv 里，
     两条腿种子流不同就根本不会撞上同一 (stage, seed)）；
   · eval   ⇒ 兄弟课 `eval_log.jsonl` 里 `(wver16, stage, seed)` 相同的 `event:"eval"` 行。
3. **找到就取，找不到就本课自采**（前课被 `keep_iters` 清了、权重没撞上、兄弟课还没跑到
   —— 一律按 miss，不降级、不伪造）。

零新增存储：不拷贝到缓存、不写索引；rollout 命中才把那一局的 shard 拷进本课波次目录
（本来也要落盘），eval 命中才移植一行账。扫描成本 = 每轮一遍候选目录（按目录名匹配，
命中才读 manifest），与既有的 `eval_done_keys`/`load_ledger` 全量读账同一个量级。

三条接缝（`reuse_rollouts` / `reuse_index`+`reuse_rollout` / `reuse_eval_round`）：派发前过滤
（`trainer/dispatch`，与 `local_slots` 无关）· 本机槽单局直取（`trainer/queue_local`）· eval 行移植
（`trainer/eval_dispatch`）。

**为什么这仍然合法**：per-tick 奖励由训练侧读 `metrics.npy` 现算（`worker/ppo/np_core`），
同一局的 obs/action/metrics 与奖励公式无关 ⇒ 换课重算奖励是精确的；键里那套「去奖励的
语料身份」由 `episode_identity_fp` 承担。盘上只有 `reward.npy` 的语料（打包端已算奖励）
**不借**（那是别的课的奖励，数值上无从分辨）。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from common.log import log

NN_DIR = Path(__file__).resolve().parents[1]  # nn-training/
REPO_ROOT = NN_DIR.parent
CURRICULA_DIR = NN_DIR / "curricula"

#: 命中局的结算节点标签（观测面）：它不是采样机 ⇒ 不入 `dist-agent-meta.jsonl` 的机器健康账，
#: 会以 `nodes.reuse` 出现在评估 summary 里（一眼看出这一轮有多少局是借来的）。
REUSE_NODE = "reuse"

#: 每轮最多扫多少个兄弟课程目录（用户 2026-10-22 口径：**100**——复用面本身就是「全库同族」，
#: 扫不全就等于白算；扫描成本随课程数线性增，而命中才读 manifest）。
#: 超了只意味着少借几局（按 miss 自采），绝不影响正确性。
MAX_SOURCES = 100
#: 全量索引的条目上限（约几百 KB 内存；超了就不再收——少借几局而已，绝不影响正确性）。
MAX_INDEX = 4000

_OFF = frozenset({"0", "off", "false", "no", "-", "none"})


def enabled(args: Any) -> bool:
    """复用开关（`--reuse-siblings 0` 或 env `NN_REUSE_SIBLINGS=0` ⇒ 关，旧行为逐字节）。"""
    raw = str(os.environ.get("NN_REUSE_SIBLINGS", "")).strip().lower()
    if raw in _OFF:
        return False
    try:
        return int(getattr(args, "reuse_siblings", 1) or 0) != 0
    except (TypeError, ValueError):
        return True


def _resolve_dir(raw: str) -> Path | None:
    """课程里写的相对路径 → 真目录（依次试 cwd / nn-training / 仓库根，与训练进程同口径）。"""
    if not raw:
        return None
    p = Path(raw)
    cands = [p] if p.is_absolute() else [Path.cwd() / p, NN_DIR / p, REPO_ROOT / p]
    for c in cands:
        if c.is_dir():
            return c
    return None


def sibling_courses(course: Any) -> list[tuple[str, Path]]:
    """同族课程（一局身份相同）+ 它们的 traj 根；按课名排序，最多 `MAX_SOURCES` 个。

    读 curricula 下全部课程文件，单个坏文件（BC 课/找不到 level）跳过而不是炸训练。
    """
    if course is None:
        return []
    from biz.corpus_fp import episode_identity_fp  # 延迟 import：它经 pydantic 拖 numpy
    from biz.course_resolve import load_course

    try:
        want = episode_identity_fp(course)
    except Exception:
        return []
    out: list[tuple[str, Path]] = []
    for f in sorted(CURRICULA_DIR.glob("*.jsonc")):
        try:
            other = load_course(f)
        except Exception:
            continue
        if other.name == course.name or other.mode != course.mode:
            continue
        try:
            if episode_identity_fp(other) != want:
                continue
        except Exception:
            continue
        root = _resolve_dir(str(other.traj))
        if root is None:
            continue
        out.append((other.name, root))
        if len(out) >= MAX_SOURCES:
            break
    return out


def _shard_key(name: str) -> tuple[int, int] | None:
    """`rl_s{stage}_seed{seed}` → (stage, seed)；其余名字 → None（不解析就不猜）。"""
    if not name.startswith("rl_s") or "_seed" not in name:
        return None
    head, tail = name[4:].split("_seed", 1)
    try:
        return int(head), int(tail)
    except ValueError:
        return None


def rollout_sources(
    pairs: list[tuple[int, int]] | None, wver: str, args: Any
) -> dict[tuple[int, int], tuple[Path, Path, str]]:
    """{pair: (shard 目录, 该波 `_rl_report.json`, 源课名)}——只认 `wver` 相同的现成局。

    `pairs=None` ⇒ 建**全量索引**（≤ `MAX_INDEX` 条，一次扫描供整轮查表）；给 `pairs` ⇒
    只找这几局（一次性开销更小）。
    扫描口径：每个兄弟工作目录走一遍，按**目录名**匹配 (stage, seed)，命中才读 manifest
    校验 wver / 奖励载体（`reward.npy` 载体不借：那是别的课算的奖励）。
    起始分布课程一律不借 rollout（切点随 `(rotate_seed, it, stage, seed)` 变，跨课对不上
    就等于换了起跑状态）；eval 不受这条影响。
    """
    course = getattr(args, "course_obj", None)
    if course is None or not enabled(args) or getattr(course, "state_init", None) is not None:
        return {}
    want = set(pairs) if pairs is not None else None
    found: dict[tuple[int, int], tuple[Path, Path, str]] = {}
    for name, root in sibling_courses(course):
        for dirpath, _dirs, filenames in os.walk(root):
            key = _shard_key(os.path.basename(dirpath))
            if key is None or key in found or (want is not None and key not in want):
                continue
            if "manifest.json" not in filenames or "metrics.npy" not in filenames:
                continue
            shard = Path(dirpath)
            if (shard / "reward.npy").exists():
                continue  # R2：预置奖励是**别的课**算的，绝不借
            try:
                mm = json.loads((shard / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if mm.get("wver") != wver:
                continue
            report = shard.parent / "_rl_report.json"
            if not report.is_file():
                continue
            found[key] = (shard, report, name)
            if len(found) >= _index_cap(want):
                return found
    return found


def _index_cap(want: set[tuple[int, int]] | None) -> int:
    """停扫阈值：给定 pairs 就按对数停；全量索引按 `MAX_INDEX` 停（内存有界）。"""
    return len(want) if want is not None else MAX_INDEX


def reuse_rollout(
    src: tuple[Path, Path, str],
    wdir: Path,
    *,
    stage: int,
    seed: int,
    course_fp: str,
    corpus_fp: str,
) -> dict | None:
    """把兄弟课那一局的 shard 拷进本课波次目录（D14 改戳）并返回本局报告；失败 ⇒ None。

    只替代**计算**：shard 照常落在本课 traj（`completed_pairs` / PPO 装载 / 断点续跑看到
    的形态与真跑同构），`_rl_report.json` 照写（调用方读面不变），报告聚合照旧把这局算进账。
    D14 改戳合法：调用方保证 `episode_identity_fp` 相同（只差 reward），这里把**本课**的
    `corpus_fp`/`course_fp` 写回去；不改就是把源课的 reward 血缘带进来（混训拒收）。
    """
    shard_src, report_path, source = src
    dst = wdir / f"rl_s{stage}_seed{seed}"
    t0 = time.time()
    try:
        shutil.copytree(shard_src, dst, dirs_exist_ok=True)
        mm_p = dst / "manifest.json"
        mm = json.loads(mm_p.read_text(encoding="utf-8"))
        if course_fp:
            mm["course_fp"] = course_fp
        if corpus_fp:
            mm["corpus_fp"] = corpus_fp
        mm["reused"] = {"from": source}
        mm_p.write_text(json.dumps(mm, indent=2), encoding="utf-8")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        shutil.rmtree(dst, ignore_errors=True)
        log(
            f"[reuse] WARN rollout 借局失败（按 miss 自采）：s{stage}/seed{seed}"
            f" ← {source} — {type(e).__name__}: {e}"
        )
        return None
    copy_sec = round(time.time() - t0, 3)
    out = dict(report)
    src_sec = float(out.get("elapsedSec") or 0.0)
    out["elapsedSec"] = copy_sec  # 本端实际花的墙钟（源局的耗时另列）
    out["reused"] = {"from": source}
    if src_sec:
        out["reusedSourceElapsedSec"] = src_sec
    (wdir / "_rl_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (wdir / "rollout.log").write_text(
        f"reused from {source} s{stage}/seed{seed} (source elapsedSec={src_sec:.3f})"
        " — 本局未重算\n",
        encoding="utf-8",
    )
    return out


def reuse_rollouts(
    tasks: list[tuple[int, int]], traj_dir: Path, wver: str, args: Any
) -> list[tuple[int, int]]:
    """派发前整批过一遍兄弟课工作目录：命中局的 shard 拷进本课 `rw{i}/`，**不进任务队列**。

    返回剩下来要自采的局（命中局数在 `[reuse]` 日志行里）。拷进来的 shard 由
    `resumed_manifests` 从盘上收进本轮聚合报告（与「重启撞上已落盘局」同一条路）⇒ 本函数
    不返回报告、不改记账口径。

    为什么走这条而不是 `run_local_rollout` 的重用分支：那条腿只在**本机槽**上生效，节点
    集群模式下 `local_slots` 可能为 0，命中局会被照样派给远端重算一遍。派发前过滤与派给
    谁无关——命中局根本不进队列（连 POST/取包都不发生）。目录名用 `rw{i}` 而不是 `w{i}`：
    `w{i}` 是本机槽的写目录（`run_local_rollout` 往里写 `rollout.log`/`_rl_report.json`），
    同名会和并发本机局互相覆盖。
    """
    if not tasks:
        return tasks
    sources = {t: s for t, s in rollout_sources(tasks, wver, args).items() if t in set(tasks)}
    if not sources:
        return tasks
    from worker.cmd import corpus_fp_for_args, course_fp_for_args

    cfp, cfp2 = course_fp_for_args(args), corpus_fp_for_args(args)
    taken: set[tuple[int, int]] = set()
    for i, task in enumerate(tasks):
        src = sources.get(task)
        if src is None:
            continue
        got = reuse_rollout(
            src, traj_dir / f"rw{i}", stage=task[0], seed=task[1], course_fp=cfp, corpus_fp=cfp2
        )
        if got is not None:
            taken.add(task)
    if taken:
        log(
            f"[reuse] rollout: {len(taken)}/{len(tasks)} 局直接取自同族课程的工作目录"
            "（同权重 + 同 (stage,seed) ⇒ 不派发、不重算；shard 已拷入本课 traj）"
        )
    return [t for t in tasks if t not in taken]


def find_eval_rows(
    pairs: list[tuple[int, int]], wver16: str, args: Any
) -> dict[tuple[int, int], tuple[dict, str]]:
    """{pair: (兄弟课的 eval 行, 源课名)}——同 wver 前缀 + 同 (stage, seed) 的现成读数。

    跳过带 `source` 的行（B/C evalboard 批是另一套 harness，不能当 A-eval 的账）。
    """
    course = getattr(args, "course_obj", None)
    if course is None or not enabled(args):
        return {}
    want = set(pairs)
    out: dict[tuple[int, int], tuple[dict, str]] = {}
    for name, root in sibling_courses(course):
        ledger = root / "eval_log.jsonl"
        if not ledger.is_file():
            continue
        try:
            lines = ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event") != "eval":
                continue
            if row.get("wver") != wver16 or "source" in row:
                continue
            try:
                key = (int(row["stage"]), int(row["seed"]))
            except (KeyError, TypeError, ValueError):
                continue
            if key not in want or key in out:
                continue
            out[key] = (row, name)
        if len(out) == len(want):
            break
    return out


def transplant_row(row: dict, *, it: int, source: str) -> dict:
    """把兄弟课那一行的读数原样搬过来，只换**本轮**的身份字段（iter/节点/时间/来源）。

    读数本体（outcome/win/cleared/dims/各 metrics 族列）逐字段来自源行——不重算、不补 0。
    """
    out = dict(row)
    out["iter"] = it
    out["node"] = REUSE_NODE
    out["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out["wallSec"] = None
    out["reusedFrom"] = source
    return out


def reuse_eval_round(
    tasks: list[tuple[int, int]], *, key16: str, args: Any, it: int
) -> tuple[list[tuple[int, int]], dict[tuple[int, int], dict]]:
    """派发前整轮过一遍兄弟课程的账：返回（剩下来要跑的，命中局 → 已移植的行）。"""
    rows = find_eval_rows(tasks, key16, args)
    if not rows:
        return tasks, {}
    reused = {task: transplant_row(row, it=it, source=src) for task, (row, src) in rows.items()}
    todo = [t for t in tasks if t not in reused]
    log(
        f"[reuse] eval it{it}: 同族课程里已现成 {len(reused)}/{len(tasks)} 局"
        "（同权重 + 同 (stage,seed) ⇒ 不重算；行照常落账）"
    )
    return todo, reused


def land_reused_eval(
    reused: dict[tuple[int, int], dict],
    record_with_retry: Any,
    seen: set[tuple[int, int]],
    lock: Any,
) -> None:
    """命中局预落账：行照落（进 `landed` 收工口径），但**不占节点、不碰网络**。

    `seen` 同步登记——否则收官行会报「认领≠落盘」（那是给丢掉的真局用的告警）。
    """
    for task, row in reused.items():
        with lock:
            seen.add(task)
        record_with_retry({}, REUSE_NODE, task, None, row=row)

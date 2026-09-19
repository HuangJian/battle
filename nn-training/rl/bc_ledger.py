"""bc_ledger —— BC 轮账本的最小读/写面（BC 编排器与只读计划视图**共用一份**）。

**为什么单独一层**：BC 的「跑到第几轮」与 RL **不同源** —— RL 的指针来自 `iteration` 事件
（`rl/train_ledger.py` 的 `LedgerView.next_it`），BC 的指针来自 `bc_round_completed`。若两边各写
一份读法，同一份 `training_log.jsonl` 就会被两个读者用不同规则解释（第二份真相）—— 而这两边
今天必须一致：单进程 supervisor（`rl/loop_serve.py`）用本模块决定「这门课下一步跑哪轮」，
BC 编排器（`run_bc.py`）用它决定「断点续跑从哪轮接着」与控制台卡片显示什么。

只做四件事，且**只依赖标准库**（读面要能在只读 CLI 里被快速 import）：

  · `append_ledger`    —— 追加一条事件（单行 append 本身就是 BC 账本的写协议）；
  · `completed_rounds` —— 已完成的 BC 轮号；
  · `bc_progress`      —— 指针（第一个未完成的轮）+ 完成数 + 是否全轮跑完；
  · `inflight_jobs`    —— 已发布未回传的 job（`job_pending ∖ 终局`；控制台「在等什么」的 BC 半）。

**刻意不塞进 `rl/train_ledger.py` 的 `LedgerSpec`**：那边的字段（kl / entropy / 样本量 / 连击 /
verdict）对 BC 没有任何意义，硬塞会把「RL 判据」变成两义（`ignored_events` 的沉默递增已经是
那份视图对 BC 的正确态度）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: BC 轮完成事件名（唯一判据；外加 `job_completed` 标记 job 已收口）。
ROUND_DONE_EVENT = "bc_round_completed"
#: job 收口事件名（`remote.hub_client.mark_job_completed` 写；用于分辨「这轮的 job 还没回来」）。
JOB_DONE_EVENT = "job_completed"
#: job 发布事件名（`remote.hub_client.publish_job` 写；「已发布未回传」的起点）。
JOB_PENDING_EVENT = "job_pending"
#: job 作废事件名（`cancel_stale_jobs` 写：滞后迭代/旧 runId 的 pending 被打扫）。
#: 它与收口**同为终局**——只排除收口会把作废的 job 永远读成「在飞」。
JOB_CANCELLED_EVENT = "job_cancelled"


def append_ledger(jsonl_path: str | Path, event: dict) -> None:
    """追加一条账本事件（父目录按需创建）。"""
    p = Path(jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(jsonl_path: str | Path) -> list[dict]:
    """读全部事件（坏行/半写行**跳过**，不抛——账本随时可能被别的进程追加）。"""
    p = Path(jsonl_path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def completed_rounds(jsonl_path: str | Path) -> set[int]:
    """账本里已完成的 BC 轮号（断点续跑依据）。"""
    out: set[int] = set()
    for e in read_events(jsonl_path):
        if e.get("event") != ROUND_DONE_EVENT:
            continue
        try:
            out.add(int(e["it"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def scan_jobs(jsonl_path: str | Path) -> tuple[dict[str, dict], set[str], set[str]]:
    """一遍扫出 job 的三件事 → `(发布事件表, 已收口 id, 已终局 id)`。

    `completed_jobs`（收口 ⇒ 这一轮的活干完了）与 `inflight_jobs`（未终局 ⇒ 还在等回传）
    共用这**一遍**扫描：同一份账本被两个读者各扫一遍，除了白花 IO，还会在「谁是终局」这种
    细节上漂开（作废算不算终局？只算收口会让作废的 job 永远显示在飞——于是有人只看一处就改了）。

    `job_id` 不是字符串的事件一律跳过（旧世代/半写行）：一个假 jid 会让「在飞」多一条幽灵。
    """
    pending: dict[str, dict] = {}
    done: set[str] = set()
    terminal: set[str] = set()
    for e in read_events(jsonl_path):
        jid = e.get("job_id")
        if not isinstance(jid, str):
            continue
        ev = e.get("event")
        if ev == JOB_PENDING_EVENT:
            pending[jid] = e
        elif ev == JOB_DONE_EVENT:
            done.add(jid)
            terminal.add(jid)
        elif ev == JOB_CANCELLED_EVENT:
            terminal.add(jid)
    return pending, done, terminal


def completed_jobs(jsonl_path: str | Path) -> set[str]:
    """账本里已收口（落位验收完成）的 job id —— 用来分辨「这轮已发布未回」与「历史残留」。"""
    return scan_jobs(jsonl_path)[1]


def inflight_jobs(jsonl_path: str | Path) -> list[dict]:
    """已发布**未回传**的 job（`job_pending ∖ (job_completed ∪ job_cancelled)`）。

    返回 `[{"jid", "it", "ts"}, …]`，按 `(it, jid)` 升序（同一轮多条时顺序稳定）。

    这是 BC 的**在飞事实**：BC ≠ RL——RL 把在飞集写进 `it<N>/commit_journal.jsonl`，BC 走
    `publish_job` 的 `job_pending` 账本事件（没有 journal）。控制台的「在等什么」必须由它
    与 RL 的 journal **归一成同一种记录**，否则 BC 课会被读成「没有外部等待」（而它正等着
    GPU 回传）——那就把这张卡片存在的意义丢掉了。

    `it` 缺失/非整数记 0（**保留**而不是丢弃）：宁可显示一条来历不明的在飞，也不要把一个
    真在跑的 job 藏起来（藏起来 = 卡片告诉你「没在等」，那是谎话）。
    """
    pending, _done, terminal = scan_jobs(jsonl_path)
    out: list[dict] = []
    for jid, e in pending.items():
        if jid in terminal:
            continue
        raw_it = e.get("it")
        out.append(
            {
                "jid": jid,
                "it": int(raw_it)
                if isinstance(raw_it, int) and not isinstance(raw_it, bool)
                else 0,
                "ts": e.get("ts"),
            }
        )
    out.sort(key=lambda r: (int(r["it"]), str(r["jid"])))
    return out


@dataclass(frozen=True)
class BcProgress:
    """BC 轮进度（指针 + 完成数 + 上界）。"""

    #: 下一个要跑的轮号（第一个未完成；全跑完 = `iters + 1`）。
    next_it: int
    #: 已完成的轮数。
    done: int
    #: 已完成的轮号集合（诊断/展示用）。
    completed: frozenset[int]
    #: 课程的轮数上界（0 = 未知，此时指针 = 最大已完成轮 + 1）。
    iters: int

    @property
    def finished(self) -> bool:
        """全轮跑完（轮数已知，且已完成的轮数够上界）。`iters = 0`（未知）**永远不算跑完**。"""
        return self.iters > 0 and self.done >= self.iters


def bc_progress(jsonl_path: str | Path, iters: int = 0) -> BcProgress:
    """读账本 → 进度。**保守方向**：读不到/空账本 ⇒ 指针 = 1（宁可重做首轮，不可误跳）。

    `iters <= 0` = 调用方不知道课程轮数（只读 CLI 不愿为一个数字 import 课程配置）⇒ 指针
    取「最大已完成轮 + 1」，**不**推断是否跑完（`finished` 恒 False —— 与 `already_done`
    同一条诚实性：算不出来的判据不得当成完成）。
    """
    done = completed_rounds(jsonl_path)
    n = int(iters or 0)
    if n <= 0:
        return BcProgress(
            next_it=(max(done) + 1 if done else 1),
            done=len(done),
            completed=frozenset(done),
            iters=0,
        )
    nxt = next((it for it in range(1, n + 1) if it not in done), n + 1)
    return BcProgress(next_it=nxt, done=len(done), completed=frozenset(done), iters=n)

"""commit_journal.py —— I1 快速缓解：PPO 提交序列的 WAL（started/done）。

「rollout 150/150 settled → PPO 提交返回前」进程消失的两起事故（c5-tick / c6-bonus）
里，重启后无从知道**上一轮提交进行到哪**。WAL 语义：

  start(phase, round)   进入提交序列前落 started（write + flush + fsync）
  finish(phase, round)  权重落位 + 账本结算后落 done
  attach(phase, round)  补事实（job_id / dispatch / attempt）——**不改状态机**（R2b）
  pending()             最后一条 op 是 start 的 (phase, round) = 未完成提交
  inflight()            pending + 它们的 job_id / 派发对象 / 开始时刻（R2b 的在飞集）

重启后 pending() 非空的处理（roadmap I1）：
  · 本地路径（ppo_local）——既有 epoch 级 ppo_ckpt 断点续跑（`_serial_ppo` 传
    `ckpt_path=traj/ppo_ckpt`）从最近 checkpoint 继续未完成批次；
  · 远端路径（ppo_remote）——按同 it 重发 job（publish 幂等键 = run_id+it+wver，
    `verify_and_land` 三重校验防错位落盘）。

纪律：WAL 行写入**绝不抛**——journal 失败只记日志、绝不阻断训练（与 forensics 同纪律：
诊断手段不是新故障面）。坏行（截断/半写）跳过不抛，WAL 本身按「最后一条 wins」读。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rl.log import log

_EVENT = "commit_journal"


class CommitJournal:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _append(self, op: str, phase: str, round_key: str, extra: dict | None) -> None:
        line = {
            "event": _EVENT,
            "op": op,
            "phase": phase,
            "round": str(round_key),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(),
        }
        if extra:
            line.update(extra)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            needs_newline = False
            if self._path.exists() and self._path.stat().st_size > 0:
                with open(self._path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    needs_newline = f.read(1) != b"\n"
            with open(self._path, "a", encoding="utf-8") as f:
                if needs_newline:
                    # 上次进程死在写入中间留下的半行：先补换行，不让它吞掉本条记录
                    # （否则半行+本条合并成坏行，finish 也会被读丢）。
                    f.write("\n")
                f.write(json.dumps(line, ensure_ascii=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            log(f"[commit-journal] {op} 落盘失败（{e}）——WAL 缺失，继续训练")

    def start(self, phase: str, round_key: str, **extra: object) -> None:
        """进入提交序列（重复 start 同一 round 合法——重试轮覆盖语义，最后一条 wins）。"""
        self._append("start", phase, round_key, extra or None)

    def finish(self, phase: str, round_key: str, **extra: object) -> None:
        """提交序列完成（权重落位 + 账本结算后调用）。"""
        self._append("finish", phase, round_key, extra or None)

    def attach(self, phase: str, round_key: str, **extra: object) -> None:
        """给**已开始**的提交补事实（R2b，2026-09-18）：job_id / 派发给了谁 / 重试次数。

        为什么要单独一个 op：job_id 是 `publish_job()` 的**返回值**（start 时还没有），
        而重启后「我发过哪份 job、在等谁回传」正是最需要知道的事——否则只能靠
        「按同 it 重发，publish 幂等键相同」侥幸兑过（幂等成立，但日志里看不到真相）。

        对状态机**零影响**：`pending()` 只看 start/finish，attach 不改变「未完成」判定
        （因此可以随时补，重试轮补多次也只是最后一条 wins）。写入绝不抛（同 start/finish）。
        """
        self._append("attach", phase, round_key, extra or None)

    def _scan(self) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], dict]]:
        """扫一遍 WAL → (每个 (phase, round) 的最后 op, 每个键最后一次 attach 的字段)。

        坏行（json 截断/半写）跳过不抛；读不到文件返回空（从未提交过）。
        """
        last: dict[tuple[str, str], str] = {}
        meta: dict[tuple[str, str], dict] = {}
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue  # 坏行跳过（进程死于写入中间时的半行）
                    if not isinstance(rec, dict) or rec.get("event") != _EVENT:
                        continue
                    key = (str(rec.get("phase", "")), str(rec.get("round", "")))
                    op = str(rec.get("op", ""))
                    if op == "attach":
                        # 状态机不受 attach 影响（只看 start/finish）；只记元数据。
                        extra = {k: v for k, v in rec.items() if k not in ("event", "op", "phase", "round")}
                        meta.setdefault(key, {}).update(extra)
                        continue
                    last[key] = op
        except OSError:
            return {}, {}
        return last, meta

    def pending(self) -> list[dict]:
        """未完成的提交：按 (phase, round) 取最后一条 op，仍为 start 的那些。

        坏行（json 截断/半写）跳过不抛；读不到文件返回 []（从未提交过 = 无 pending）。
        """
        last, _meta = self._scan()
        return [{"phase": p, "round": r} for (p, r), op in sorted(last.items()) if op == "start"]

    def inflight(self) -> list[dict]:
        """**在飞集**：已开始未完成的提交 + 它们的 job_id / 派发对象 / 开始时刻。

        R2b 的用户口径：任务要自带「本轮已发布的 job_id、在等谁回传」。重启后
        `_commit_journal` 用这一条日志把「上一轮到底在等谁」说清楚，而不是只报
        一个 phase/round 对（旧 `pending()` 的输出）。与 `pending()` **同一个真相**
        （都在扫这同一个 WAL），不新建第二份登记表。
        """
        last, meta = self._scan()
        out: list[dict] = []
        for (phase, rnd), op in sorted(last.items()):
            if op != "start":
                continue
            rec: dict = {"phase": phase, "round": rnd}
            rec.update(meta.get((phase, rnd), {}))
            out.append(rec)
        return out

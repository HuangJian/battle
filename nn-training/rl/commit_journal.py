"""commit_journal.py —— I1 快速缓解：PPO 提交序列的 WAL（started/done）。

「rollout 150/150 settled → PPO 提交返回前」进程消失的两起事故（c5-tick / c6-bonus）
里，重启后无从知道**上一轮提交进行到哪**。WAL 语义：

  start(phase, round)   进入提交序列前落 started（write + flush + fsync）
  finish(phase, round)  权重落位 + 账本结算后落 done
  pending()             最后一条 op 是 start 的 (phase, round) = 未完成提交

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

    def pending(self) -> list[dict]:
        """未完成的提交：按 (phase, round) 取最后一条 op，仍为 start 的那些。

        坏行（json 截断/半写）跳过不抛；读不到文件返回 []（从未提交过 = 无 pending）。
        """
        last: dict[tuple[str, str], str] = {}
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
                    last[key] = str(rec.get("op", ""))
        except OSError:
            return []
        return [{"phase": p, "round": r} for (p, r), op in sorted(last.items()) if op == "start"]

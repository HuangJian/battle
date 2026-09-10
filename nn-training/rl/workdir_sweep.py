"""workdir_sweep.py — trainer 侧本地采样路径的临时目录收敛（§374 同步，2026-09-08）。

sampler-agent 的 workdir 收敛（tools/agent/workdir-cleanup.ts，§374）清理被杀进程
残留的 game-* 临时目录；trainer 侧 local_slots 直跑（rl/queue_local.run_local_rollout）
的对应物 = it{N}/w{idx}/ 波次目录——每局一个、next_idx 单调递增永不复用（dispatch.py
worker 的 `_idx = next_idx[0]` 路径）。

- **成功局**：w{idx}/ 含 _rl_report.json + rl_s*_seed*/ 完整 shard。PPO load_episodes
  与 completed_pairs 都以 manifest 为准，**必须保留**（断点续跑依赖，删了会重采）。
- **失败/废弃局**：子进程 rc!=0 或被杀 → w{idx}/ 只有部分 shard + rollout.log、无
  _rl_report.json。dispatch 回队后用新 w{idx} 重跑，旧目录无人清理——每次失败尝试
  留一个孤儿波次目录（MAX_TASK_ATTEMPTS=3 时单局最多 2 个，长跑累积）。

本模块 plan_（纯函数）与 sweep_（IO 执行）分离，单测共享（同 repo 纯函数先例：
queue_local.pick_tail_race / race_tier_ok）。sweep_failed_wave_dirs 在每轮迭代
收尾（loop_guards._rotate_cleanup，当前 it 目录全部局已结算、无在飞子进程）删除
无 _rl_report.json 的 w* 目录——删除失败（沙箱保护/占用）跳过，训练照常。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from platform_utils import rmtree_best_effort

REPORT_FILE = "_rl_report.json"
WAVE_RE = re.compile(r"^w\d+$")


def plan_failed_wave_dirs(iter_dir: Path) -> list[Path]:
    """返回 iter_dir 下无 _rl_report.json 的 w<idx> 目录（失败/废弃局的孤儿波次目录）。

    成功局以 _rl_report.json 为完整标记：PPO 与 resume 都消费它，**永不删**。返回
    按名字典序（确定性，便于测试与日志复现）。
    """
    if not iter_dir.is_dir():
        return []
    out = []
    for p in sorted(iter_dir.iterdir()):
        if p.is_dir() and WAVE_RE.match(p.name) and not (p / REPORT_FILE).exists():
            out.append(p)
    return out


def sweep_failed_wave_dirs(iter_dir: Path, log: Callable[[str], None] = print) -> int:
    """删除失败/废弃的波次目录，返回实际删除数。

    best-effort：单目录删除失败（沙箱删除保护拦截/句柄占用）静默跳过、不计数，
    与 loop_core._prepare_iter_dir 的「沙箱删除保护拦截时跳过（训练照常）」同策略。
    """
    n = 0
    for p in plan_failed_wave_dirs(iter_dir):
        # rmtree_best_effort 以**返回值**表达成败（不再抛异常），故计数必须挂在
        # 返回值上——否则沙箱删除保护拦截时会把没删掉的目录也算进 n。
        try:
            removed = rmtree_best_effort(p)
        except BaseException:
            removed = False
        if removed:
            n += 1
            log(f"[workdir-sweep] removed failed wave dir {p.name} (no {REPORT_FILE})")
    return n

"""loop_guards_sweep —— TrainingGuardsSweep mixin：轮级磁盘回收（S4 第二十三刀）。

`_rotate_cleanup`：keepIters 目录轮转（`it{N}/`）+ H9 远端 job 目录清理 + §374 失败波次
孤儿目录清扫。判据 = 「本轮已结算、这些产物不再被消费」；删除被沙箱删除保护拦截时静默
降级（`rmtree_best_effort`），磁盘轮转照旧、绝不阻断训练。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platform_utils import rmtree_best_effort
from rl.log import log
from rl.workdir_sweep import sweep_failed_wave_dirs


class TrainingGuardsSweep:
    """轮级磁盘回收 mixin（keepIters 轮转 / job 目录 / 孤儿波次清扫）。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _traj_root: Any
    _traj_dir: Any

    def _rotate_cleanup(self, it: int) -> None:
        """keepIters 目录轮转（沙箱删除保护拦截时静默降级，磁盘轮转照旧）。

        H9（review-hy）：远程模式的 job 目录（remote-jobs/<job_id>/）不在 it{N}/
        下，keep_iters 轮转不会带走它。每轮 payload.zip + result.json + ppo_ckpt_remote
        线性增长。扩展清理：扫描 job 目录，清理已完成且迭代 <= it - keep_iters 的 job。

        ★ 2026-09-21（§3）：这段原先挂在 `args.ppo == 'remote'` 下 —— 单一 PPO 路径下
        PPO 恒在节点上跑、旗标已删，该判据恒真/恒假的两种写法都是错，故改为**无条件**
        （job 目录清理是每个训练进程都要做的事）。
        """
        args = self.args
        if args.keep_iters > 0:
            for old in self._traj_root.glob("it*"):
                try:
                    n_old = int(old.name[2:])
                except ValueError:
                    continue
                if n_old <= it - args.keep_iters:
                    # 沙箱删除保护拦截时跳过（磁盘轮转降级）
                    rmtree_best_effort(old, ignore_errors=True)
            # H9：清理旧 job 目录（已完成的 job 不再需要 payload 与结果文件）
            job_root = Path(
                getattr(args, "remote_job_root", "") or str(self._traj_root / "remote-jobs")
            )
            if job_root.exists():
                cutoff = it - args.keep_iters
                for jd in job_root.iterdir():
                    if not jd.is_dir():
                        continue
                    mf = jd / "manifest.json"
                    if not mf.exists():
                        continue
                    try:
                        mm = json.loads(mf.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    jit = mm.get("it")
                    if not isinstance(jit, int):
                        continue
                    if jit <= cutoff and (jd / "result" / "result.json").exists():
                        rmtree_best_effort(jd, ignore_errors=True)
            # §374 同步（2026-09-08）：本地采样波次目录收敛——本轮已全部结算（无在飞
            # 子进程），清失败/废弃局的孤儿 w* 目录（无 _rl_report.json：部分 shard +
            # rollout.log 是死重，PPO/resume 都不消费）。完整波次目录是语料，永不删
            # （resume 依赖）；删除失败（沙箱保护/占用）跳过，训练照常。
            try:
                swept = sweep_failed_wave_dirs(self._traj_dir, log=log)
                if swept:
                    log(f"[run_rl] workdir-sweep it{it}: {swept} failed wave dir(s) removed")
            except BaseException:
                pass

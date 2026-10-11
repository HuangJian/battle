"""tests/remote/test_train_core_shards_root.py —— PPO 装载根（`plan/rollout-topup-fastpath` §3.5）。

评审 P0-1 的承重面：节点自己的 PPO 读的是 `remote/train_core.py` 里那个 `shards_root`，而取消搬回
（§3.5）之后 rollout 的产出**不再回 job 目录**（它住在节点本地盘的轮 scratch 目录里）⇒ 读取根必须
由**产出方**显式给出，否则整轮以 0 局语料开练（静默训空）。

判据分两层（本文件守第一层 = 决策逻辑；第二层是产出方那一侧，见 `test_round_ctx.py` /
`test_rollout_scratch.py` 的 `out["shards_root"]` 断言）：

  * 给了 `shards_root` ⇒ 用它（换根档）；
  * 没给 / 是空串 / 整个 `iter_info is None`（老调用方、老 hub、回退档）⇒ **逐字**回落 `job_dir`。

`run_training_core` 全链要真 torch + 真课程（不在单测里跑）⇒ 这里只盯那个纯函数，接线处是
一行调用（`shards_root = ppo_shards_root(job_dir, iter_info)`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.train_core import ppo_shards_root


def test_scratch_root_wins_over_the_job_dir(tmp_path: Path) -> None:
    """换根档：产出方给的轮 scratch 目录就是装载根（**不许**被 job_dir 盖掉）。"""
    job = tmp_path / "job"
    scratch = tmp_path / "local-disk" / "nn-rollout-run-abcd1234"
    assert ppo_shards_root(job, {"shards_root": str(scratch)}) == str(scratch)


def test_missing_key_and_old_callers_fall_back_to_the_job_dir(tmp_path: Path) -> None:
    """老调用方/回退档：没有 `iter_info`、没有这个键、空串 —— 三种都**逐字**回落 `job_dir`。"""
    job = tmp_path / "job"
    assert ppo_shards_root(job, None) == str(job)
    assert ppo_shards_root(job, {}) == str(job)
    assert ppo_shards_root(job, {"shards_root": ""}) == str(job), "空串不是根（不落到 cwd）"
    assert ppo_shards_root(job, {"shards_root": None}) == str(job)


def test_it_takes_a_str_or_path_and_returns_str(tmp_path: Path) -> None:
    """两种类型都吃（产出方现在给 `str`，将来给 `Path` 也不破）且恒回 `str`（`load_episodes` 吃的是 str）。"""
    job = tmp_path / "job"
    assert isinstance(ppo_shards_root(job, {}), str)
    assert isinstance(ppo_shards_root(str(job), {}), str)
    # 平台中性：断言钉的是「Path 进 ⇒ str 出、**逐字透传**」，不是钉 POSIX 形态
    # （Windows 的 `str(Path("/x/y"))` = `\x\y`；拿字面量 "/x/y" 比会在 Windows 恒红）
    p = Path("/x/y")
    assert ppo_shards_root(str(job), {"shards_root": p}) == str(p)

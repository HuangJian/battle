"""worker/events::write_rollout_end —— **采集收官即刻**落账（用户 2026-10-19 口径）。

## 为什么有这一行

一轮 13 步里 `record_iteration`（写 `iteration` 事件）排第 ⑪，前面压着 `ppo`
（远端模式 = 发布 job → 等云机跑完 → 权重回传，几十分钟量级）。而**控制台的走势图
/指标表只读账本里的 `iteration` 行** ⇒ 采集早就结束了，操作员却要等 W(it+1) 回来才
多一个点，看起来像"卡住"。

## 为什么**另起事件名**而不是提前写 `iteration`

`iteration` 是**断点续跑的推进判据**：`worker/resume.py` 只认它算
`last_completed_iter`。若在 PPO 之前写它，进程一旦死在「采集完 → 梯度步提交前」，
续跑就会**跳过这一轮**——采集白烧，账本还自证跑过了。

⇒ 另起 `rollout_end`：续跑 / 门禁照旧忽略未知事件，只有**控制台指标读面**消费它。
本轮 `iteration` 行落账后它自动被覆盖（同 iter 取信息量更大的那条）。

## 本文件钉什么

1. schema：采集侧字段齐全，PPO 侧键**一个都不写**（缺键 = 空，读面按 NaN 缺口处理，
   不冒充 0）；
2. 续跑安全：`LedgerView.apply_event` 登记轮号但**不推进** `next_it` / `iterations_n`；
3. `_record_rollout` 幂等：同一 it 重复落账只写一行（让步后重入 / 重放不重复记账）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_core import TrainingLoop
from worker.events import write_rollout_end
from worker.train_ledger import LedgerView, load_ledger

TS = "2026-10-19 07:00:00"

#: 采集侧报告（`worker/reports.py` 的 rollout report 形状，只带本文件关心的键）。
REPORT = {
    "games": 200,
    "winRate": 0.42,
    "outcomes": {"stage_clear": 84},
    "scoreStats": {"mean": 0.75, "std": 0.11, "min": 0.0, "max": 1.0},
    "dimMeans": {"progress": 0.3, "loot": 0.2, "baseIntegrity": 0.9},
    "totalSamples": 12345,
    "totalTicks": 90000,
    "pure_collect_sec": 28.0,
    "dist_phase_sec": 2.5,
}


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "iters": 100,
        "out": "tmp/x/weights.json",
        "traj": "tmp/x",
        "mode": "per-tick",
        "lr": 1e-4,
        "mb": 2,
        "epochs": 1,
        "remote_hub_url": "",
        "remote_token": "",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_rollout_end_schema_has_only_collect_side_fields(tmp_path: Path) -> None:
    """采集侧字段齐全；PPO 侧键**一个都不写**（缺键 ⇒ 读面 NaN 缺口，不冒充 0）。"""
    p = tmp_path / "training_log.jsonl"
    row = write_rollout_end(p, _args(), 5, REPORT, 31.5)
    assert row["event"] == "rollout_end"
    assert row["iter"] == 5
    assert row["time"] == TS or len(str(row["time"])) == 19
    # 采集侧读数
    assert row["winRate"] == 0.42
    assert row["score_mean"] == 0.75
    assert row["score_std"] == 0.11
    assert row["samples"] == 12345
    assert row["ticks"] == 90000
    assert row["rollout_sec"] == 31.5
    assert row["pure_collect_sec"] == 28.0
    assert row["dist_phase_sec"] == 2.5
    # PPO / 传输账：此刻不存在 ⇒ 不写键（写了 0 就是假零点）
    for key in ("kl", "entropy", "policy", "value", "mean_ret", "lr", "ppo_sec", "ppo_cloud_sec", "wire"):
        assert key not in row, key
    # 落盘即一行 JSON
    assert json.loads(p.read_text(encoding="utf-8").strip())["event"] == "rollout_end"


def test_rollout_end_does_not_advance_resume_pointer(tmp_path: Path) -> None:
    """★ 续跑判据不变：`rollout_end` 登记轮号，但 `next_it` / `iterations_n` 不动。

    破了这条 = 死在 PPO 前的一轮会被续跑跳过（采集白烧 + 账本自证跑过）。
    """
    p = tmp_path / "training_log.jsonl"
    write_rollout_end(p, _args(), 3, REPORT, 30.0)
    view = load_ledger(p)
    assert view.rollout_ends == {3}
    assert view.iterations_n == 0
    assert view.next_it == 1
    # 增量路径与单次扫描同口径（R2a 的三层钉法之一）
    inc = LedgerView(path=p)
    inc.apply_event({"event": "rollout_end", "iter": 3, "time": TS})
    assert inc.rollout_ends == view.rollout_ends
    assert inc.iterations_n == view.iterations_n
    assert inc.next_it == view.next_it

    # 同一 it 的 iteration 行照旧推进指针
    inc.apply_event({"event": "iteration", "iter": 3, "time": TS, "samples": 10, "epochs": 1})
    assert inc.iterations_n == 1 and inc.next_it == 4


def test_record_rollout_is_idempotent(tmp_path: Path) -> None:
    """同一 it 重复进入采集收官步 ⇒ 只落一行（让步后重入 / 重放不重复记账）。"""
    loop = TrainingLoop(_args(traj=str(tmp_path), out=str(tmp_path / "weights.json")), None, "bun", {})
    loop._jsonl_path = tmp_path / "training_log.jsonl"
    loop._report = dict(REPORT)
    loop._rollout_sec = 31.5
    loop._ledger = LedgerView(path=loop._jsonl_path)
    loop._record_rollout(6)
    loop._record_rollout(6)  # 让步后重入
    lines = [x for x in loop._jsonl_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["iter"] == 6
    # 换一轮照旧要写
    loop._record_rollout(7)
    lines = [x for x in loop._jsonl_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [json.loads(x)["iter"] for x in lines] == [6, 7]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""R1-10 负向范围用例：**别把 eval 侧的长尾竞速一起删了**。

2026-09-22 的 P3 删的是 **hub 的竞速广播判定**（`race_decision` / `race_mode` / `--race`）。
仓库里还住着**同类机制**：`trainer/queue_local.py` 的长尾竞速（`pick_tail_race` /
`race_tier_ok` / `pick_race_target`，rollout 侧消费者 `trainer/dispatch.py` /
`trainer/batch_runner.py`）。两套机制名字像、目的像、**不该同生共死**——P3 的删除清单是
逐符号列出的，本文件就是那条边界的机械门禁：后人大扫除时顺手删掉这一套，会在**这里**
红，而不是在几个旬之后的评测里以「长尾任务又排到天亮」的形式悄悄复发。

2026-11-04（任务级统一派发）：eval 侧的「尾巴多副本竞速」不再经 `trainer/queue_local`
的轮级判据，而是住进 `worker/eval_arbiter.py` 的**副本道**（`replica_candidates` /
`claim_replica` / `_replica_grant`）——语义不变（唯一局派完、仍有空闲槽 ⇒ 同一局复制到
别的节点抢单），只是粒度从「一轮的尾巴」提到「所有轮的在飞局」。守卫因此钉两处：
符号本身还在（rollout 侧仍消费），且 eval 侧的消费者是新家的副本道。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trainer.queue_local as Q


def test_eval_tail_race_symbols_still_exist() -> None:
    """三样符号仍在（且可调用）——P3 的删除范围**不**包含它们。"""
    for name in ("pick_tail_race", "race_tier_ok", "pick_race_target"):
        assert hasattr(Q, name), f"eval 长尾竞速的 {name} 被误删了（R1-10 的边界）"
        assert callable(getattr(Q, name))


def test_eval_replica_lane_still_consumes_the_tail_race() -> None:
    """消费者还在接线（删了符号但断了调用点同样是回归）。

    2026-11-04 起 consumer 不再逐符号 import `queue_local`（副本道要跨轮挑靶，轮级
    判据搬不动）——判据改成新家的副本道三件套在场，且派发器不再自己长一份。
    """
    import ast

    arb = (ROOT / "worker" / "eval_arbiter.py").read_text(encoding="utf-8")
    tree = ast.parse(arb)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"replica_candidates", "claim_replica", "_replica_grant"} <= funcs, (
        "eval 长尾竞速的消费者没接线（副本道三件套缺了）"
    )
    # 反向：派发器不许自己再实现一遍轮级竞速（单一所有者 = 仲裁器）。
    disp = (ROOT / "trainer" / "eval_dispatch.py").read_text(encoding="utf-8")
    disp_tree = ast.parse(disp)
    used = {n.name for n in ast.walk(disp_tree) if isinstance(n, ast.FunctionDef)}
    used |= {n.id for n in ast.walk(disp_tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(disp_tree) if isinstance(n, ast.Attribute)}
    assert used.isdisjoint({"pick_tail_race", "race_tier_ok"}), (
        "eval_dispatch 又长出了轮级竞速（副本道应只在 worker/eval_arbiter.py）"
    )

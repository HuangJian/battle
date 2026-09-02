"""regen_reward_golden —— 重新生成 reward golden 文件（评审 P1-8 golden-file 口径）。

一次性用当前公式引擎对固定 counter 向量 + outcome 组合算出期望值，存
`tests/golden/reward_golden.json`；pytest 断言 Python 逐位复现入库值。

**何时重生成**：公式引擎语义变化 / 指标向量重排（`metrics_version` bump）/
课程配置公式改动之后。重生成 = 语义变更声明，须在 commit message 与
DECISIONS.md 记录原因，不得随手重跑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from rl.config import load_course
from rl.reward_library import METRICS_DIM, build_reward_fn

GOLDEN_DIR = ROOT / "tests" / "golden"
GOLDEN_PATH = GOLDEN_DIR / "reward_golden.json"


#: golden 固定向量生成器 —— 与 test_reward_golden._v7_corpus 逐位同源（同 seed）。
#: **不得改动 seed/形状**，否则整个 golden 需要重生成。
def v7_corpus(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = np.zeros((n, METRICS_DIM), dtype=np.float64)
    m[:, 0] = rng.integers(0, 36000, n)  # ticks
    m[:, 1] = rng.integers(0, 25, n)  # kills
    m[:, 2] = rng.integers(0, 4, n)  # lives
    m[:, 5] = rng.integers(0, 300, n)  # playerShots
    m[:, 7] = rng.integers(0, 10, n)  # powerUpsCollected
    m[:, 8] = rng.integers(0, 12, n)  # powerUpsSpawned
    m[:, 11] = rng.integers(0, 2, n)  # baseAlive
    m[:, 12] = rng.integers(0, 9, n)  # baseWallTotal
    m[:, 13] = rng.integers(0, 9, n)  # baseWallIntact
    m[:, 14] = rng.random(n) * 5000  # basePressureSum
    m[:, 15] = rng.integers(0, 6000, n)  # basePressureSamples
    m[:, 16] = rng.choice([-1.0, 0.0, 500.0, 2000.0, 36000.0], n)  # firstKillTick
    m[:, 20] = rng.integers(1, 21, n)  # enemyTotal
    return m


def main() -> None:
    from tests.test_reward_golden import _golden_vectors  # reuse 同一输入定义

    cache = {}
    cases = []
    for case in _golden_vectors():
        name = case["course"]
        if name not in cache:
            cache[name] = build_reward_fn(load_course(name).reward_spec())
        fn = cache[name]
        r = fn(
            np.asarray(case["metrics"], dtype=np.float64),
            case["outcome"],
            case["gated"],
            case["it"],
        )
        cases.append(
            {
                "course": name,
                "outcome": case["outcome"],
                "gated": case["gated"],
                "it": case["it"],
                "metrics": case["metrics"],
                "reward": [float(x) for x in r],
            }
        )
    doc = {"metrics_version": 2, "generated_by": "scripts/regen_reward_golden.py", "cases": cases}
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"golden regenerated: {GOLDEN_PATH} ({len(cases)} cases)")


if __name__ == "__main__":
    main()

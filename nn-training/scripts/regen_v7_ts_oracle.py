"""regen_v7_ts_oracle —— 生成 v7 对 TS oracle 的保真度 golden。

流程：
  1. 固定 counter 行（PhiCounters 语义，含 null/哨兵分支）→ counters.jsonl
  2. `bun tools/diag/v7-phi-oracle.ts`（rl-reward.ts::phiNow）→ phi 列表
  3. 合并存 `tests/golden/v7_phi_ts_oracle.json`（metrics 21 维行 + ts phi）

pytest `test_v7_ts_oracle_fidelity` 断言 Python v7 公式/内置 ≤1e-9。重生成须在
DECISIONS.md 记录原因（这是对账基准，不是随手能刷的数字）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.reward_library import METRIC_INDEX, METRICS_DIM

GOLDEN_DIR = ROOT / "tests" / "golden"
OUT_PATH = GOLDEN_DIR / "v7_phi_ts_oracle.json"

#: 21 维 → PhiCounters 字段名（见 tools/sim/rl-reward.ts::PhiCounters）。
#: 与 Python v7 公式共用同一份 metrics 行。
_MAP = {
    "enemyTotal": "enemyTotal",
    "ticks": "ticks",
    "kills": "kills",
    "lives": "lives",
    "playerShots": "playerShots",
    "powerUpsSpawned": "powerUpsSpawned",
    "powerUpsCollected": "powerUpsCollected",
    "baseAlive": "baseAlive",  # 0/1 → TS bool（漏掉会让 F3 门控 ×0.1 + baseIntegrity 归零！）
    "baseWallTotal": "baseWallTotal",
    "baseWallIntact": "baseWallIntact",
}


def _rows(n: int = 256, seed: int = 20260902) -> list[np.ndarray]:
    """固定分布的 21 维行（null/哨兵全覆盖）——**勿改 seed**，重生成即换基准。"""
    rng = np.random.default_rng(seed)
    rows: list[np.ndarray] = []
    for _ in range(n):
        m = np.zeros(METRICS_DIM)
        m[0] = rng.integers(0, 36000)  # ticks
        m[1] = rng.integers(0, 25)  # kills
        m[2] = rng.integers(0, 4)  # lives
        m[3] = rng.integers(0, 200)  # playerHits
        m[5] = rng.integers(0, 300)  # playerShots
        m[7] = rng.integers(0, 10)  # powerUpsCollected
        m[8] = rng.integers(0, 12)  # powerUpsSpawned
        m[11] = rng.integers(0, 2)  # baseAlive
        m[12] = rng.integers(0, 9)  # baseWallTotal
        m[13] = rng.integers(0, 9)  # baseWallIntact
        m[14] = rng.random() * 5000  # basePressureSum
        m[15] = rng.integers(0, 6000)  # basePressureSamples
        m[16] = rng.choice([-1.0, 0.0, 500.0, 2000.0, 36000.0])  # firstKillTick
        m[20] = rng.integers(1, 21)  # enemyTotal
        rows.append(m)
    return rows


def metrics_to_counters(m: np.ndarray, start_lives: int = 3) -> dict:
    """21 维行 → PhiCounters（rl-reward.ts 输入）。firstKillTick -1 → null。"""
    c: dict = {ts: float(m[METRIC_INDEX[py]]) for py, ts in _MAP.items()}
    c["startLives"] = start_lives
    c["basePressureMean"] = float(m[METRIC_INDEX["basePressureSum"]])  # ⚠️ 见 oracle 头注释
    c["basePressureSamples"] = float(m[METRIC_INDEX["basePressureSamples"]])
    fk = float(m[METRIC_INDEX["firstKillTick"]])
    c["firstKillTick"] = None if fk == -1.0 else fk
    return c


def main() -> None:
    rows = _rows()
    jsonl = "\n".join(json.dumps(metrics_to_counters(m)) for m in rows)
    oracle_script = REPO / "tools" / "diag" / "v7-phi-oracle.ts"
    proc = subprocess.run(
        ["bun", str(oracle_script)],
        input=jsonl,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"v7-phi-oracle 失败: {proc.stderr[:2000]}")
    phi = json.loads(proc.stdout)
    assert len(phi) == len(rows), (len(phi), len(rows))
    doc = {
        "generated_by": "scripts/regen_v7_ts_oracle.py → tools/diag/v7-phi-oracle.ts",
        "note": "basePressureMean 载入 basePressureSum（oracle 命名坑见工具头）；"
        "firstKillTick:-1(=无首杀) 转 null。Python 侧指标矩阵逐行对应。",
        "metrics": [m.tolist() for m in rows],
        "phi": phi,
    }
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    # 本地自检：Python v7 对 golden 立即对账
    sys.path.insert(0, str(ROOT))
    from rl.config import load_course
    from rl.reward_library import build_reward_fn

    fn = build_reward_fn(load_course("s4b").reward_spec())
    M = np.asarray(doc["metrics"], dtype=np.float64)
    got = fn.phi(M, 1)
    exp = np.asarray(doc["phi"], dtype=np.float64)
    delta = float(np.max(np.abs(got - exp)))
    print(f"oracle golden regenerated: {OUT_PATH} ({len(rows)} rows, {len(phi)} phi)")
    print(f"python-v7 vs ts-oracle max|Δ| = {delta:.3e} (需 ≤1e-9)")
    if delta > 1e-9:
        raise SystemExit("对账失败：Python v7 偏离 TS oracle >1e-9 —— 修复后再提交")


if __name__ == "__main__":
    main()

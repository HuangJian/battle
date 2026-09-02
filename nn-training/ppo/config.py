"""PPO 超参 SSOT + 三后端分叉表（plan/python-refactor.md P1-1 轻量版，2026-09-02）。

三后端（per-tick / intent / goal）的超参各自散落定义在 ppo/{engine,intent,goal}.py，
且存在**未解释的数学分叉**（历史遗留）。本模块：

  * **记录**每后端实际生效的超参（单一事实来源，数值必须与本地定义一致）；
  * **显式列出分叉与判定**——后续调参必须回到本表对齐口径，不得只改本地常量；
  * `assert_backend_constants()` 供测试守护：本地常量被改动而未同步本表 → 变红。

**分叉表（判定依据）**：

| 超参 | per-tick | intent | goal | 判定 |
|------|----------|--------|------|------|
| ENT_COEF | 0.01 | 0.08 | 0.08 | ⚠ 8× 差异无法用动作空间解释（最大熵 2.30 vs 2.08 几乎相同）——intent 是事故驱动（it22→it28 坍缩修复），per-tick 未做同等压力测试 |
| LR | 3e-4 | 1e-4 | 1e-4 | 刻意（intent/goal 强策略微调，注释有据） |
| normalize_ret | False | True | True | ⚠ per-tick 未归一（engine.py load_episodes normalize_ret=False），value 目标量级 0.1-0.3 偏小 |
| target_kl 早停 | 无 | 0.02 | 0.02 | ⚠ per-tick 无 KL 护栏（P0-3 后口径） |
| γ/λ CLI 可调 | 是 | 否 | 否 | 扫参不便（低优先） |
| RNG 播种 | main 一次 | main 一次 | main 一次 | ✓ 已统一（P1-5 修复：update 内重播种会让流式每波 permutation 相同） |

**历史锚点**（改超参先读）：per-tick 的 GAMMA 0.99→0.995 / VF_COEF 0.5→1.0 是
IT1-IT68 未收敛的教训（engine.py:74-81 注释）；intent 的 ENT_COEF 0.02→0.08 是
M8 坍缩修复（intent.py 注释）。
"""

from __future__ import annotations

# ---- 每后端超参记录（数值必须与 ppo/{engine,intent,goal}.py 本地定义一致）----
BACKEND_PARAMS: dict[str, dict[str, float | int | bool | None]] = {
    "per-tick": {
        "GAMMA": 0.995,
        "LAM": 0.95,
        "CLIP_EPS": 0.2,
        "VF_COEF": 1.0,
        "ENT_COEF": 0.01,
        "LR": 3e-4,
        "MAX_GRAD_NORM": 1.0,
        "normalize_ret": False,
        "target_kl": None,  # per-tick 无 target_kl 早停
    },
    "intent": {
        "GAMMA_TICK": 0.995,
        "LAM": 0.95,
        "CLIP_EPS": 0.2,
        "VF_COEF": 1.0,
        "ENT_COEF": 0.08,
        "LR": 1e-4,
        "MAX_GRAD_NORM": 1.0,
        "INTENT_DIM": 8,
        "TARGET_KL": 0.02,
        "normalize_ret": True,
    },
    "goal": {
        "GAMMA_TICK": 0.995,
        "LAM": 0.95,
        "CLIP_EPS": 0.2,
        "VF_COEF": 1.0,
        "ENT_COEF": 0.08,
        "LR": 1e-4,
        "MAX_GRAD_NORM": 1.0,
        "BOARD": 26,  # goal 动作空间 676 = 26×26（schema.BOARD 同步值）
        "TARGET_KL": 0.02,
        "normalize_ret": True,
    },
}


def assert_backend_constants() -> None:
    """守护：三后端本地常量与 BACKEND_PARAMS 记录一致（测试入口）。

    任一处调参只改本地定义而未同步本表 → AssertionError 列出差异。
    """
    import ppo.engine as eng
    import ppo.goal as goal
    import ppo.intent as intent

    modules = {
        "per-tick": (eng, BACKEND_PARAMS["per-tick"]),
        "intent": (intent, BACKEND_PARAMS["intent"]),
        "goal": (goal, BACKEND_PARAMS["goal"]),
    }
    for name, (mod, params) in modules.items():
        for key, want in params.items():
            if not hasattr(mod, key):
                continue  # 行为参数（如 normalize_ret，是 load_episodes 的入参而非模块
                # 常量）由分叉表文档约束，getattr 无法检查
            got = getattr(mod, key)
            if got != want:
                raise AssertionError(
                    f"ppo/{name}.py 常量 {key}={got!r} ≠ config.py 记录 {want!r} —— "
                    f"调参后必须同步 ppo/config.py 分叉表"
                )

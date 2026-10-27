"""tests/helpers/stream_spec.py —— `kind=iter` 逐局流式采集的 **spec 工厂**（单测与 e2e 共用）。

为什么要一份工厂：流式采集的单元用例（`tests/worker/test_stream_collect.py`）与集成用例
（`e2e/test_stream_collect_e2e.py`）必须拿**同形状**的 spec —— `volume` 块要过
`validate_runtime_volume` 的同源复算，argv 要过 `validate_rollout_spec` 的白名单/路径/唯一
(stage,seed) 校验。各写一份迟早漂成「单测绿的形状、链路上根本不合法」。

走的就是生产那条路：`volume_block`（与计划导出同一族纯函数）→ `pairs_for`（初波对集）
→ `iter_spec`（运行时块 + 校验）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.plan import iter_spec, pairs_for
from worker.volume_alloc import alloc_games_by_stage, est_hi
from worker.volume_quota import target_per_stage

#: 三关、总目标 900 ⇒ 每关 quota 300（数字好手算：100/25/50 样本/局都能整除）。
STAGES: list[int] = [2000, 2001, 2002]
TARGET = 900
ESTS: dict[int, int] = {2000: 100, 2001: 100, 2002: 100}
ROTATE = 20261027
IT = 7
WVER = "wver-stream"
#: 协议白名单里的导出器脚本（自造 spec 必须用它，否则 `validate_rollout_spec` 拒收）。
SCRIPT = "tools/sim/export-rl-rollout.ts"


def template(stage: int = 0, *, stage_json: str = "") -> list[str]:
    """一条最小 argv 模板（`retarget_argv` 会替换 stage/seed/out/wver）。"""
    row = [
        SCRIPT,
        "--weights",
        "init_weights.json",
        "--stages",
        str(int(stage)),
        "--seeds",
        "1",
        "--out",
        "w0",
        "--wver",
        "",
    ]
    if stage_json:
        row += ["--stage-json", stage_json]
    return row


def volume_block(
    *,
    stages: list[int] | None = None,
    ests: dict[int, int] | None = None,
    target: int = TARGET,
    topup: dict | None = None,
    it: int = IT,
    rotate: int = ROTATE,
) -> dict:
    """与计划导出同一个纯函数族造**运行时** `volume` 块（可过 `validate_runtime_volume`）。"""
    stage_list = [int(s) for s in (stages or STAGES)]
    est_map = dict(ests or ESTS)
    games = alloc_games_by_stage(
        stage_list, int(target), ests_hi={s: est_hi(int(est_map.get(s, 0))) for s in stage_list}
    )
    return {
        "target_transitions": int(target),
        "stages": stage_list,
        "per_stage_quota": target_per_stage(int(target), len(stage_list)),
        "games_per_stage_by_stage": games,
        "est_s_by_stage": {s: int(est_map.get(s, 0)) for s in stage_list},
        "est_hi_factor": 1.15,
        "topup": {
            "enabled": True,
            "max_batches": 3,
            "last_lo_factor": 0.85,
            "max_games_per_stage": 0,
            **(topup or {}),
        },
        "it": int(it),
        "rotate_seed": int(rotate),
    }


class Course:
    """最小课程替身：只回答「这一关的自定义 stageJson」（`worker.plan.stage_json_of` 的契约）。"""

    def __init__(self, stage_json: dict[int, str] | None = None) -> None:
        self._m = dict(stage_json or {})

    def stage_json(self, stage: int) -> str:
        return str(self._m.get(int(stage), ""))


def build_spec(
    *,
    vol: dict | None = None,
    stage_json: dict[int, str] | None = None,
    workers: int = 4,
) -> dict:
    """`volume` 块 → `pairs_for`（初波对集）→ `iter_spec`（含同源校验）。

    `workers` 写进 spec（节点侧 `slots` 由它夹出；流式档的并发 = 槽位 ⇒ 这里就是池宽）。
    """
    block = volume_block() if vol is None else vol
    stage_list = [int(s) for s in block["stages"]]
    plan = {
        "workers": int(workers),
        "rotate_seed": int(block["rotate_seed"]),
        "volume": block,
        # `argv_for_iteration` 只按 pair 下标取模板（不足时回落 tmpl[0]）；自定义关的
        # `--stage-json` 由**课程**注入（与生产同一条路）。
        "argv_template": [template(stage_list[0])],
    }
    return iter_spec(
        plan,
        int(block["it"]),
        pairs_for(plan, int(block["it"])),
        wver=WVER,
        course=Course(stage_json),
    )


def stage_of(argv: list[str]) -> int:
    return int(argv[argv.index("--stages") + 1])


def seed_of(argv: list[str]) -> int:
    return int(argv[argv.index("--seeds") + 1])

"""任务键（与 `tools/agent/sampler-agent.ts::taskKey` 的逐字节镜像）。

取消通道（POST /v1/cancel）必须送 agent 认可的同一个 key；键配方已有 TS 实现，
Python 只做镜像（tests/common/test_task_cancel.py 用固定向量对拍）。
"""

from __future__ import annotations


def task_key(
    iter_id: str,
    mode: str,
    kind: str,
    stage: int,
    seed: int,
    stage_json_hash: str = "",
    course_fp: str = "",
    decision_events: str = "",
    decision_k: str = "",
    label_mode: str = "",
    # 评估/部署链推理规则（当前只有顶钢空枪门）：同一 (stage, seed) 在开/关两态
    # 是不同的局 ⇒ 仅激活时并入 `:skipSteelShots`（老键逐字节不变）。
    skip_steel_only_shots: str = "",
    # 拆砖免绕路：同一 (stage, seed) 在开/关两态是不同局 ⇒ 仅激活时进 `:breakBricks`。
    break_bricks_on_path: str = "",
    # B8 通用废射门：同一 (stage, seed) 开/关是不同局 ⇒ 仅激活时进 `:suppressUselessFire`。
    suppress_useless_fire: str = "",
    # B10 预测门：同一 (stage, seed) 开/关是不同局 ⇒ 仅激活时进 `:suppressLeadMissFire`。
    suppress_lead_miss_fire: str = "",
    # Hybrid opening：同一 (stage, seed) 的 bare / hybrid 是不同局 ⇒ 仅激活时进
    # `:hybridOpen`（老键逐字节不变）。
    opening_connect: str = "",
) -> str:
    if stage_json_hash and course_fp:
        base = f"{iter_id}:{mode}:{kind}:{stage}:{seed}:{stage_json_hash}:{course_fp[:16]}"
    elif stage_json_hash:
        base = f"{iter_id}:{mode}:{kind}:{stage}:{seed}:{stage_json_hash}"
    elif course_fp:
        base = f"{iter_id}:{mode}:{kind}:{stage}:{seed}:c{course_fp[:16]}"
    else:
        base = f"{iter_id}:{mode}:{kind}:{stage}:{seed}"
    return (
        base
        + (":de1" if decision_events else "")
        + (f":k{decision_k}" if decision_k else "")
        + (f":lm{label_mode}" if label_mode else "")
        + (":skipSteelShots" if skip_steel_only_shots else "")
        + (":breakBricks" if break_bricks_on_path else "")
        + (":suppressUselessFire" if suppress_useless_fire else "")
        + (":suppressLeadMissFire" if suppress_lead_miss_fire else "")
        + (":hybridOpen" if opening_connect else "")
    )

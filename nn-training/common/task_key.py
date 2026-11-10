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
    )

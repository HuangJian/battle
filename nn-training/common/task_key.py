"""任务键（与 `tools/agent/sampler-agent.ts::taskKey` 的逐字节镜像）。

取消通道（POST /v1/cancel）必须送 agent 认可的同一个 key；键配方已有 TS 实现，
Python 只做镜像（tests/common/test_task_cancel.py 用固定向量对拍）。

## 为什么收参数对象（P0-3）

它原来是 16 个参数：5 个身份位（`iter_id` / `mode` / `kind` / `stage` / `seed`）+ 11 个
**带缺省的开关**，而开关里 9 个是同一形状的「激活 ⇒ 追加一段 `:xxx`」字符串（
`skip_steel_only_shots` / `break_bricks_on_path` / ... / `corpus_fmt`）。那个形状的问题是
**位置相邻且全同型**：多传/漏传一位不报错，只是键变长或变短 —— 而键的后果是「取消帧
打不中这一局」（训练继续白烧卡）或「两态不同的局撞成同一个键」（replay 语料与 full 语料
互相覆盖）。

`TaskKeySpec` 把这些开关变成**按名**的字段，且缺省与旧签名逐字一致（老键逐字节不变）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class TaskKeySpec:
    """一局任务的身份与开关（`task_key` 只收它）。"""

    iter_id: str
    mode: str
    kind: str
    stage: int
    seed: int
    #: 关卡 JSON 指纹（有它 ⇒ 键里带完整 hash 段）。
    stage_json_hash: str = ""
    #: 课程指纹（只取前 16 位，且 course-only 形状多一个 `c` 前缀）。
    course_fp: str = ""
    decision_events: str = ""
    decision_k: str = ""
    label_mode: str = ""
    # 评估/部署链推理规则（当前只有顶钢空枪门）：同一 (stage, seed) 在开/关两态
    # 是不同的局 ⇒ 仅激活时并入 `:skipSteelShots`（老键逐字节不变）。
    skip_steel_only_shots: str = ""
    # 拆砖免绕路：同一 (stage, seed) 在开/关两态是不同局 ⇒ 仅激活时进 `:breakBricks`。
    break_bricks_on_path: str = ""
    # B8 通用废射门：同一 (stage, seed) 开/关是不同局 ⇒ 仅激活时进 `:suppressUselessFire`。
    suppress_useless_fire: str = ""
    # B10 预测门：同一 (stage, seed) 开/关是不同局 ⇒ 仅激活时进 `:suppressLeadMissFire`。
    suppress_lead_miss_fire: str = ""
    # Hybrid opening：同一 (stage, seed) 的 bare / hybrid 是不同局 ⇒ 仅激活时进
    # `:hybridOpen`（老键逐字节不变）。
    opening_connect: str = ""
    # replay 语料（plan/replay-corpus.plan.md P2）：同 (stage, seed) 的 full/replay 是
    # 不同产物 ⇒ 仅 replay 时进 `:cfr`（full 键逐字节不变）。
    corpus_fmt: str = ""


def task_key(spec: TaskKeySpec) -> str:
    """键配方（与 TS 侧逐字节一致）—— 参数全在 `TaskKeySpec`。"""
    if spec.stage_json_hash and spec.course_fp:
        base = (
            f"{spec.iter_id}:{spec.mode}:{spec.kind}:{spec.stage}:{spec.seed}"
            f":{spec.stage_json_hash}:{spec.course_fp[:16]}"
        )
    elif spec.stage_json_hash:
        base = (
            f"{spec.iter_id}:{spec.mode}:{spec.kind}:{spec.stage}:{spec.seed}"
            f":{spec.stage_json_hash}"
        )
    elif spec.course_fp:
        base = (
            f"{spec.iter_id}:{spec.mode}:{spec.kind}:{spec.stage}:{spec.seed}"
            f":c{spec.course_fp[:16]}"
        )
    else:
        base = f"{spec.iter_id}:{spec.mode}:{spec.kind}:{spec.stage}:{spec.seed}"
    return (
        base
        + (":de1" if spec.decision_events else "")
        + (f":k{spec.decision_k}" if spec.decision_k else "")
        + (f":lm{spec.label_mode}" if spec.label_mode else "")
        + (":skipSteelShots" if spec.skip_steel_only_shots else "")
        + (":breakBricks" if spec.break_bricks_on_path else "")
        + (":suppressUselessFire" if spec.suppress_useless_fire else "")
        + (":suppressLeadMissFire" if spec.suppress_lead_miss_fire else "")
        + (":hybridOpen" if spec.opening_connect else "")
        + (":cfr" if spec.corpus_fmt else "")
    )

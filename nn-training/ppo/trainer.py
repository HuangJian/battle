"""PPO 训练公共样板（B1，2026-09-02）。

三后端（engine per-tick / intent / goal）的 update 主体存在 ~67% 行级重复，
但重复集中在**纯样板**（tensor 转换、stats 聚合、空回退、heartbeat 日志），
真正有数学差异的部分（warmup 冻结、kickstarting KL、target_kl 早停、engage
辅助 loss、变步长 GAE）各后端不同且行为敏感。

合并策略（避免过度抽象）：**只抽取纯样板**到本模块，三后端 update 引用——
行为逐字节不变（数值相同）；数学差异部分保留在各自后端（三端仍在演进，
强行模板化会把真实差异压成复杂钩子，过早固化）。

新增后端时：样板直接用本模块；数学逻辑按需实现。
"""

from __future__ import annotations

from typing import Any


def tensored_chunks(chunks: list[dict], device, memory_format=None) -> list[dict]:
    """numpy minibatch chunks → torch（一次性转换，避免每 epoch 重复）。

    chunk 字段全部为 numpy 数组（obs/scalars/actions/logprobs/adv/ret/mask）。

    memory_format（F2/review-hy 2.08×）：传入 torch.channels_last 时，obs 张量
    转换后即转换为 NHWC 内存布局——mkldnn 在 NHWC 下走快路径，实测 2.08× 加速
    （4.29 → 2.06 s/chunk）。对 GPU 也是快路径。调用方（ppo/engine.py 等）在
    模型构建后调用一次 model.to(memory_format=channels_last) 即可。
    """
    import torch

    out: list[dict] = []
    for c in chunks:
        item: dict = {}
        for k, v in c.items():
            t = torch.from_numpy(v).to(device)
            if memory_format is not None and k == "obs":
                t = t.contiguous(memory_format=memory_format)
            item[k] = t
        out.append(item)
    return out


def aggregate_stats(stats: list[dict[str, float]], keys: list[str]) -> dict[str, float]:
    """stats 列表按键聚合（均值）；空列表返回零值 dict（断点续跑 0 epoch 路径）。"""
    n = len(stats)
    if not n:
        return {k: 0.0 for k in keys}
    return {k: sum(s[k] for s in stats) / n for k in keys}


def empty_agg(keys: list[str]) -> dict[str, Any]:
    """空 chunk 时的零聚合（各后端 update 的空输入回退）。"""
    return {k: 0.0 for k in keys}

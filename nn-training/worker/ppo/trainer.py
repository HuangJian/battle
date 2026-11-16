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

import numpy as np


def tensored_batch(item: dict, device, memory_format=None) -> dict:
    """单批 numpy dict → torch dict（P3 lazy loader 的逐批上卡）。

    与 `tensored_chunks` 的逐字段规则**逐字相同**：`torch.from_numpy(v).to(device)`，
    `memory_format` 只作用于 `obs`。只读数组（`np.load(mmap_mode="r")` 的切片视图）
    先复制成可写 —— 否则 torch 会对每批发 "not writable" UserWarning（BC lazy
    路径同样的 `_writable` 处理）。fancy index 产出的数组本来就可写，这里零成本。
    """
    import torch

    out: dict = {}
    for k, v in item.items():
        if not v.flags.writeable:
            v = np.array(v)
        t = torch.from_numpy(v).to(device)
        if memory_format is not None and k == "obs":
            t = t.contiguous(memory_format=memory_format)
        out[k] = t
    return out


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


def corpus_samples(corpus: Any) -> int:
    """语料总行数：lazy 语料读 `.samples`（**不物化**）；eager episode 列表求和。"""
    n = int(getattr(corpus, "samples", 0) or 0)
    return n or int(sum(e["obs"].shape[0] for e in corpus))


def chunk_count(chunks: Any) -> int:
    """minibatch 数：lazy chunks 读 `.sizes`（不物化），eager 列表直接 len。"""
    sizes = getattr(chunks, "sizes", None)
    return len(sizes) if sizes is not None else len(chunks)


def chunk_rows(chunks: Any) -> int:
    """实际训练步数：lazy chunks 带 `.sizes`（不物化），eager 列表按 obs 行数求和。"""
    sizes = getattr(chunks, "sizes", None)
    if sizes is not None:
        return int(sum(int(x) for x in sizes))
    return int(sum(c["obs"].shape[0] for c in chunks))


def loader_desc(chunks: Any) -> str:
    """PPO 装载器读数：`eager` 或 `lazy peak=…MB`（plan/replay-corpus D10）。"""
    name = str(getattr(chunks, "loader", "eager"))
    peak = getattr(chunks, "peak_mb", None)
    return f"{name} peak={float(peak):.0f}MB" if peak is not None else name


def aggregate_stats(stats: list[dict[str, float]], keys: list[str]) -> dict[str, float]:
    """stats 列表按键聚合（均值）；空列表返回零值 dict（断点续跑 0 epoch 路径）。"""
    n = len(stats)
    if not n:
        return {k: 0.0 for k in keys}
    return {k: sum(s[k] for s in stats) / n for k in keys}


def empty_agg(keys: list[str]) -> dict[str, Any]:
    """空 chunk 时的零聚合（各后端 update 的空输入回退）。"""
    return {k: 0.0 for k in keys}


def cat_entropy(logp: Any) -> Any:
    """类别分布的熵（`-(p·log p)` 按最后一维求和再平均）——**两个框架通用**的写法。

    为什么用位置参数 `sum(-1)`：torch 的形参名是 `dim`、Paddle 是 `axis`，只有位置形式两边
    都收 ⇒ 同一份实现能同时喂 `worker.ppo.common`（torch 侧，那里再导出）与
    `worker.ppo.engine_paddle`（Paddle 侧）。数值逐位相同（exp / 乘 / sum / mean 同序）。
    """
    return -(logp.exp() * logp).sum(-1).mean()


def cat_entropy_per(logp: Any) -> Any:
    """逐样本熵（不带 mean）——Hybrid opening 的 masked mean 用。

    `cat_entropy(logp) == cat_entropy_per(logp).mean()`；两个框架通用（位置参数 sum）。
    """
    return -(logp.exp() * logp).sum(-1)

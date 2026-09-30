"""train/bc_core.py — BC 训练器里**与 torch 无关的判据**（顶层零 torch，2026-09-26）。

## 为什么单独一个模块

`train/bc.py` 顶层 `import torch`（整个训练器都要），于是同住一个文件的**纯函数**
（「课程 JSONC / 本机 CLI / 云端 manifest 三种来路的旋钮怎么归一」）在测试里也只能连坐
torch——`tests/test_bc_course.py::test_resolve_fire_pos_weight` 因此在没有 torch 的机器上必红。
按 `ppo/np_core.py` / `train/device.py` 的同一手法抽出来；`train/bc.py` 再导出，
既有 `from train.bc import resolve_fire_pos_weight` 一行不改。

判据：**输入是标量/字典、输出是标量/异常**，不碰张量、不碰 DataLoader ⇒ 放这里。
带 torch 的（`_masked_ce` / `_bc_raw` / 训练循环）留在 `train/bc.py`。
"""

from __future__ import annotations


def resolve_fire_pos_weight(raw: object, counts: dict) -> float:
    """fire 头正例权重（2026-09-14）：`"auto"` = 训练集 neg/pos（≈12.6）；数 = 直接用；
    0/None = 关闭。为什么需要它：语料 fire 正例仅 ~7%，不加权的 CE 下 fire 头实测
    accuracy 0.770 < "永不发射"常数基线 0.927 —— 即该头是负增益。

    `raw` 来自三处：课程 JSONC（pydantic 已收窄为 float|"auto"）、本机 CLI（字符串）、
    云端 manifest（float|"auto"）—— 故字符串分支同时接受 "auto" 与数字字面量。

    非法输入（未知字符串 / 非数值类型）**响亮 raise**：旋钮写错时应当停在加载期，
    而不是静默退化成 0（= 关掉加权，训练照跑、指标悄悄变差）。
    """
    c_f = counts.get("fire") or {}
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "auto":
            pos = int(c_f.get(1, 0))
            neg = int(c_f.get(0, 0))
            return (neg / pos) if pos > 0 else 0.0
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"fire_pos_weight 只接受数字或 'auto'，收到 {raw!r}") from None
    if raw is None:
        return 0.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"fire_pos_weight 非法: {raw!r}")
    return float(raw)

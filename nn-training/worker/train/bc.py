"""
Behavior-cloning trainer (plan §NN-M1).

Consumes exported npy shards (obs / scalars / actions / masks / conditions) and
trains the 3-headed policy with masked cross-entropy:
  * Only samples where the invalid-action mask == 1 contribute to a head's loss
    (plan §1.3-4 — turn-locked / cooldown / zero-stock actions are masked out).
  * mirrorX online augmentation (prob --mirror-p) on the training split only.
  * CPU by default; `--device cuda` trains on GPU (weights export stays CPU —
    bitwise-stable files regardless of device). Small network (~77K params)
    matches the 40-120K BC sample count (underfit-safe).

Outputs:
  * <--out>  : JSON+base64 weights (plan §5), loadable by the TS runtime.
  * <--out>.pt : torch checkpoint (Python-side convenience).
"""



from __future__ import annotations

# 仓库根探测（B4，2026-09-02；2026-09-30 修）：包已安装（pip install -e .）或
# script-dir/cwd 在 nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入
# sys.path——不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正
# import，避免探针导入产生 F401。
# ⚠ 本文件搬到 `worker/<pkg>/` 后上溯层数 +1，且探针名随刀 2 的 `schema` → `common`
# 更正：算错一层会插进 `worker/`，于是 `worker/cmd.py` 遮蔽 stdlib `cmd`（torch 的
# `import pdb` 当场炸）——「兜底路径」写错从来不是小事。
import importlib.util as _ilu

if _ilu.find_spec("common") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import datetime
import math
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from typing import Any, cast

import numpy as np
import torch
import torch.nn.functional as F

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口弹出抢焦点。
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.cli import house_parser, run_main
from common.schema import OBS_SCHEMA_MAJOR
from worker.data.dataset import make_loaders
from worker.data.weights_io import load_state_into, save_weights_json
from worker.models.core import NNPolicy, param_count
from worker.models.student import PPOStudent, StudentNet

# 免 torch 的判据半边（2026-09-26，item 6f + 续扫）：BC 旋钮归一在 train/bc_core.py、
# 设备串在 train/device.py。本模块 import 后自用 + 沿用旧名对外可见（既有调用点一行不改）。
from worker.train.bc_core import (
    SMOOTH_WINDOW,
    early_stop_epoch,
    resolve_fire_pos_weight,
)
from worker.train.bc_core import (
    sanitize_json as _sanitize_json,
)
from worker.train.bc_core import (
    select_best_epoch as select_best_epoch,
)
from worker.train.bc_core import (
    smoothed_val_loss as smoothed_val_loss,
)
from worker.train.device import resolve_bc_device


def _resolve_bc_device(device: str) -> tuple[str, torch.device, bool]:
    """设备解析（BC 多卡 2026-09-13）：

      * cuda-dp：可见 2+ 卡 → DataParallel（梯度归约顺序变化，与单卡 run 数值
        不可逐位比——与 remote/worker 的 PPO DP 同一口径）；单卡/无卡**响亮退化**
        （cuda / cpu），绝不静默；
      * 其余（cuda / cpu / cuda:N）原样。

    → (规范化 device 串, torch.device, use_dp)。

    判据住在 `train/device.py`（顶层零 torch，探针可注入 ⇒ `tests/worker/test_bc_device.py`
    不必 import torch；2026-09-26 item 6f）。本函数只做两件事：**把真探针接上去** +
    把名字映成真的 `torch.device`——`cuda-dp` 不是合法 device type，多卡时必须是
    `torch.device("cuda")`（由 `tests/worker/test_bc_dp.py` 钉住），只有名字保留给日志/台账。
    """
    name, use_dp = resolve_bc_device(device)
    return name, torch.device("cuda" if use_dp else name), use_dp


def _bc_raw(model: torch.nn.Module) -> torch.nn.Module:
    """DataParallel 包装 → 解包。DP 的 state_dict 键带 "module." 前缀，落进
    weights.json 会破坏 TS 运行时格式——**落盘/恢复/计数一律用 raw**。"""
    return model.module if isinstance(model, torch.nn.DataParallel) else model


def _masked_ce(
    logits: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    class_weight: torch.Tensor | None = None,
) -> torch.Tensor:
    """合法类掩码 CE（P1-8 修复，2026-09-02）。

    旧实现 `(per * m) / m.clamp(min=1)` 是**恒等式**：m≥1 时 = per，mask 100% 无效
    ——非法类 logits 照样进 softmax 分母、被梯度推高，训练目标与 TS 推理
    （argmax(z+mask)）不一致，且 "masked accuracy" 实为普通 accuracy。

    修复：
      1) 非法类 logit 置 -1e9（softmax 分母只含合法类）；
      2) 合法类数 <2 的样本无决策信息（CE 恒 0），跳过——fire 冷却期 [release,1]
         就是这类，旧实现白白喂零梯度样本。
    """
    m = mask > 0
    keep = m.sum(dim=-1) >= 2
    if not keep.any():
        return logits.sum() * 0.0  # 无可训练样本（全为单合法类）
    z = logits.masked_fill(~m, -1e9)
    per = F.cross_entropy(z[keep], target[keep], reduction="none", weight=class_weight)
    return per.mean()


def _masked_acc(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    """合法类掩码 accuracy（P1-8 修复）：与 TS 推理同语义（非法类不参与 argmax）；
    单合法类样本跳过。旧实现同样是恒等式（mask 无效、指标名误导）。"""
    m = mask > 0
    keep = m.sum(dim=-1) >= 2
    if not keep.any():
        return 0.0
    z = logits.masked_fill(~m, -1e9)
    pred = z.argmax(dim=-1)
    return (pred[keep] == target[keep]).float().mean().item()


_WEIGHTS_MD_HEADER = """\
# NN Weights History

Trained weights are **gitignored** (never committed) and backed up manually to netdisk.
This file is the **committed registry** of every training run. Keep it in sync with the
actual `weights.*.json` files on disk.

## Naming convention

* **Versioned archive**: `weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`
  * `<YYYYMMDD-HHMMSS>` — training finish timestamp (local)
  * `<N>` — number of epochs trained
  * `<V>` — best validation loss, 4 decimals (e.g. `1.2431`)
  * Example: `weights.20260818-170055_ep40_val1.2431.json`
* **Active pointer**: `weights.json` — an exact copy of the latest versioned archive.
  It is what the TS runtime (`src/nn/infer.ts`, not yet implemented) loads. Gitignored.
* Both files live in `nn-training/`.

## Backup strategy

1. After each training run, the versioned archive + `weights.json` are produced locally.
2. **Manually** copy the new `weights.*.json` to netdisk (external backup).
3. Commit only `WEIGHTS.md` (this file) — it records which version is current.
4. On a fresh clone, weights are absent; restore the needed `weights.*.json` from netdisk
   and (optionally) copy it to `weights.json` for local inference.

## History

| trained_at | file | epochs | samples (worker/train/val) | val_loss | move/fire/item acc | git | notes |
|---|---|---|---|---|---|---|---|
"""


def _git_sha() -> str:
    """Best-effort short git sha of the repo at training time (for the registry)."""
    try:
        # **_POPEN_NO_WINDOW 使 check_output 的重载无法解析（→ Any），显式收窄为 bytes。
        raw: bytes = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
            **_POPEN_NO_WINDOW,
        )
        return raw.decode().strip()
    except Exception:
        return "n/a"


def _append_weights_md(
    out_dir: str,
    versioned_path: str,
    trained_at: str,
    args,
    sizes: dict,
    best_val: float,
    history: dict,
) -> None:
    """Append a row to the committed weights registry (WEIGHTS.md).

    Creates the file with a header (naming convention + backup strategy) on first run.
    """
    md_path = os.path.join(out_dir, "WEIGHTS.md")
    vl = history["value_loss"][-1]
    vl_s = "n/a" if (isinstance(vl, float) and math.isnan(vl)) else f"{vl}"
    row = (
        f"| {trained_at} | `{os.path.basename(versioned_path)}` | {args.epochs} "
        f"| {sizes['train']}/{sizes['val']} | {best_val:.4f} "
        f"| {history['move_acc'][-1]}/{history['fire_acc'][-1]}/{vl_s} "
        f"| {_git_sha()} | {args.notes} |\n"
    )
    if not os.path.exists(md_path):
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_WEIGHTS_MD_HEADER)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(row)


def _class_counts(dl, action_v6: bool = False) -> dict:
    """训练集各头类别计数（2026-09-14 抽出）：多数类基线与 fire 正例权重的共同数据源。

    v6 三头（2026-11-04）：语料元组多一个 `hold`（10 元组）、move 是 4 类、fire 是 3 类。
    """
    c_m: Counter[int] = Counter()
    c_f: Counter[int] = Counter()
    c_h: Counter[int] = Counter()
    for batch in dl:
        if action_v6:
            _obs, _sc, _ex, mv, fr, hd, _mm, _mf, _mh, _ret = [b for b in batch]
            for t, c in ((mv, c_m), (fr, c_f), (hd, c_h)):
                for v in t.tolist():
                    c[v] += 1
        else:
            _obs, _sc, _ex, mv, fr, _mm, _mf, _ret = [b for b in batch]
            for t, c in ((mv, c_m), (fr, c_f)):
                for v in t.tolist():
                    c[v] += 1
    return {"move": c_m, "fire": c_f, "hold": c_h} if action_v6 else {"move": c_m, "fire": c_f}


def _majority_baseline(dl, action_v6: bool = False) -> dict:
    """CE if we always predict the most frequent class per head.

    Provides a floor to interpret the trained val_loss against (audit gap #2):
    is 1.2431 near the majority-class ceiling, or is there real signal learned?
    """
    out = {}
    for name, c in _class_counts(dl, action_v6).items():
        total = sum(c.values())
        if total == 0:
            out[name] = float("nan")
            continue
        maj = c.most_common(1)[0][1]
        out[name] = -math.log(maj / total)  # CE of constant majority prediction
    return out


#: 可训练参数白名单（`--train-heads`）。trunk = `stem.* / blocks.* / fc.*`。
TRAIN_HEAD_PREFIXES: dict[str, tuple[str, ...]] = {
    "policy": ("move_head.", "fire_head.", "hold_head."),
    "value": ("value_head.",),
}


def _freeze_except(model: torch.nn.Module, keep: tuple[str, ...] | None) -> int:
    """只让 `keep` 前缀的参数保留梯度；`None` = 全训（不动）。

    冻结必须发生在建 optimizer 之前——否则 AdamW 的 weight_decay 仍会改被冻参数
    （零梯度不等于零更新）。返回保活的参数张量数。
    """
    if keep is None:
        return sum(1 for _ in model.parameters())
    n = 0
    for name, param in model.named_parameters():
        ok = name.startswith(keep)
        param.requires_grad = ok
        n += int(ok)
    if n == 0:
        raise ValueError(f"--train-heads 白名单 {keep} 没匹配到任何参数")
    return n


def _freeze_non_value(model: torch.nn.Module) -> None:
    """C9 value-only 的旧入口：等价 `_freeze_except(model, ("value_head.",))`。"""
    _freeze_except(model, TRAIN_HEAD_PREFIXES["value"])


def train(args) -> dict:
    # P2-3（2026-09-02）：可复现性补全——此前只有 torch.manual_seed，numpy/random
    # 全局 RNG 未播种（_majority_baseline 的 Counter 顺序、DataLoader worker 内
    # mirrorX 增强的 np rng 依赖全局状态时会漂移）。train DataLoader 的 shuffle
    # 已用独立 generator（data/dataset.py），此处播种保证其余随机源可复现。
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    # v6 三头（2026-11-04 落地）：语料由 `export-rl-rollout.ts` 产出，键是
    # `a_move/a_fire/a_hold + mask.npy(9)`，与 legacy 的 `actions/masks(7)` 并存。
    action_v6 = bool(getattr(args, "action_v6", False))
    train_dl, val_dl, sizes = make_loaders(
        args.data_dir,
        args.batch,
        args.val_split,
        args.mirror_p,
        args.seed,
        args.num_workers,
        action_v6=action_v6,
    )
    print(f"[train] samples total={sizes['total']} train={sizes['train']} val={sizes['val']}")

    # Majority-class baseline: a floor to interpret val_loss against (audit gap #2).
    mb = _majority_baseline(train_dl, action_v6)
    _mb_tail = f" hold={mb['hold']:.4f}" if action_v6 else ""
    print(f"[train] majority-baseline CE: move={mb['move']:.4f} fire={mb['fire']:.4f}{_mb_tail}")

    # v2（M3）：语料带 returns.npy 且 --arch student 时构建 PPOStudent，按
    # `--value-coef` 把 MC return 作为 value 头回归目标（M2 ⑥ 的 value MC 预置）。
    # 纯 BC（无 returns）沿用 StudentNet / NNPolicy 双头。
    dev_str, dev, use_dp = _resolve_bc_device(getattr(args, "device", "cpu"))
    # fire 头正例权重（2026-09-14）：默认 auto = 训练集 neg/pos。放 dev 之后构造，
    # 保证权重张量与 logits 同设备。
    counts = _class_counts(train_dl, action_v6)
    # B2（2026-11-05）：class weight **强度旋钮**。1 = 完全 inverse-frequency（旧行为），
    # 0 = 不加权，中间值 = `w ** exp`。标定判据是「预测边缘 ≈ 标签边缘」，不是 val 新低：
    # v6 fire 的三类分布 ~90:10:9 时，满额加权会把正例预测率从 9.7% 推到 23.6%。
    weight_exp = float(getattr(args, "weight_exponent", 1.0) or 0.0)
    if weight_exp < 0:
        raise ValueError(f"weight_exponent 必须 ≥ 0（得到 {weight_exp}）")
    # v6 的 hold 头 class weight（legacy 路径恒 None）——先声明类型，两个分支都要赋值
    hold_class_weight: torch.Tensor | None = None
    fire_class_weight: torch.Tensor | None = None
    if action_v6:
        # v6 的 fire 是 **3 类**（no-shot / before-turn / after-turn），二分类的 pos_weight
        # 不适用 ⇒ 用 inverse-frequency class weight（与 `export-rl-rollout` 的
        # 3152:110:28 那种极端不平衡对齐，不加权该头必退化成「永不发射」）。
        _cf = counts["fire"]
        _tot = sum(_cf.values()) or 1
        _w = [((_tot / (3 * max(1, _cf.get(k, 0)))) ** weight_exp) for k in range(3)]
        fire_class_weight = (
            torch.tensor(_w, device=dev, dtype=torch.float32) if weight_exp > 0 else None
        )
        fire_pos_weight = -1.0  # 标记「非 legacy 路径」，只用于日志
        _ch = counts["hold"]
        _htot = sum(_ch.values()) or 1
        hold_class_weight = (
            torch.tensor(
                [((_htot / (2 * max(1, _ch.get(k, 0)))) ** weight_exp) for k in range(2)],
                device=dev,
                dtype=torch.float32,
            )
            if weight_exp > 0
            else None
        )
    else:
        fire_pos_weight = resolve_fire_pos_weight(getattr(args, "fire_pos_weight", 0.0), counts)
        fire_class_weight = (
            torch.tensor([1.0, float(fire_pos_weight)], device=dev) if fire_pos_weight > 0 else None
        )
    print(
        f"[train] fire_pos_weight={fire_pos_weight:.2f}"
        f"{'（正例加权开启）' if fire_class_weight is not None else '（关闭）'}"
        f" fire counts neg={counts['fire'].get(0, 0)} pos={counts['fire'].get(1, 0)}"
    )
    if action_v6:
        print(f"[train] weight_exponent={weight_exp:.2f}（0=不加权 / 1=完全 inverse-freq）")
    # v6 的 `PPOStudent.forward` 恒返回 4 元组（move/fire/hold/value）⇒ 即使 value_coef=0
    # 也必须用 PPOStudent（StudentNet 没有 value_head），否则解包失败。
    action_v6_pre = bool(getattr(args, "action_v6", False))
    use_value = (getattr(args, "value_coef", 0.0) > 0 or action_v6_pre) and args.arch == "student"
    # B1 v6 三头（hold2/move4/fire3）：与 `build_ppo` 同一契约——开关走 CLI，
    # 落盘写进 `arch.actionV6`（`PPOStudent.arch()`），下游据此重建。
    # ⚠ 只有 `student` 家族支持（PPOStudent / StudentNet）；`NNPolicy` 是老
    # Conv 架构、只有 5/2 两头且 forward 返回二元组 ⇒ **显式拒绝**，不静默退化成 legacy。
    action_v6 = bool(getattr(args, "action_v6", False))
    # v6 数据链已接通（2026-11-04）：`npyio._load_dataset_v6` 读 `a_move/a_fire/a_hold +
    # mask.npy(9)`，`dataset.NNDataset(action_v6=True)` 返回 10 元组，本函数三头 CE。
    # 语料由 `export-rl-rollout.ts`（可加 `--teacher-argmax`）产出。
    policy_extra = bool(getattr(args, "policy_extra", False))
    # C9（value-only 预热）：policy_coef=0 时只训 value 头；策略/主干必须冻结。
    # 先拒绝无目标配置，别让零梯度 + weight_decay 静默改策略。
    policy_coef = float(getattr(args, "policy_coef", 1.0))
    if policy_coef < 0:
        raise ValueError(f"policy_coef 必须 ≥ 0（得到 {policy_coef}）")
    # `policy_coef=0` 的历史语义是 value-only；但 P2-B 的 `train_heads=policy`
    # 配 `policy_coef=0` 是另一种非法组合，留给下面的 train_heads 校验报错。
    _heads_early = str(getattr(args, "train_heads", "all") or "all")
    if policy_coef == 0.0 and _heads_early != "policy":
        _value_coef = float(getattr(args, "value_coef", 0.0) or 0.0)
        if not use_value or _value_coef <= 0:
            raise ValueError(
                "--policy-coef 0 是 value-only：需要 --arch student 且 --value-coef > 0"
                "（否则没有任何可训练目标）"
            )
    model = (
        PPOStudent(action_v6=action_v6, policy_extra=policy_extra)
        if use_value
        else (
            StudentNet(action_v6=action_v6, policy_extra=policy_extra)
            if args.arch == "student"
            else NNPolicy()
        )
    )  # type: ignore
    if getattr(args, "resume", None):
        print(f"[train] resuming from {args.resume}")
        load_state_into(model, args.resume)
    model = model.to(dev)
    raw_model = model
    # P2-B（2026-11-08）：`--train-heads` 控制可训参数范围。trunk = stem/blocks/fc。
    # 背景：pickup 窗口微调（1 万步 / 3 epoch）把 trunk 拉偏，kills −2.8、base +25pp；
    # 冻结 trunk 才能只改「捡」不动「打」。
    train_heads = str(getattr(args, "train_heads", "all") or "all")
    if train_heads not in ("all", "policy", "value"):
        raise ValueError(f"train_heads 只能是 all/policy/value（得到 {train_heads}）")
    if train_heads == "all" and policy_coef == 0.0:
        train_heads = "value"  # 兼容旧课程：policy_coef=0 即 value-only
    if train_heads == "value" and policy_coef != 0.0:
        raise ValueError("train_heads=value 需要 policy_coef=0（策略不该有梯度）")
    if train_heads == "policy" and policy_coef == 0.0:
        raise ValueError("train_heads=policy 需要 policy_coef>0（否则策略头零梯度）")
    if train_heads != "all":
        _freeze_except(raw_model, TRAIN_HEAD_PREFIXES[train_heads])
    if use_dp:
        # DP 包装：前向/反向跨卡分发（batch 均分），参数共享原地更新——raw_model
        # 的 state_dict 始终是训练终态；落盘/恢复/参数计数一律走 raw（防 "module." 前缀）。
        # cast Any：包装后经 model 走前向；落盘/恢复走 raw_model（保持具体类型，.arch() 可用）
        # `[train] DataParallel 生效（…）` 行同族同命（2026-10-06，同一条用户指令）：每轮重复的
        # 静态事实（开机横幅已印 `device=`），DP 包装本身与 `raw_model` 的落盘纪律逐字不变。
        model = cast(Any, torch.nn.DataParallel(raw_model))
    n_params = param_count(raw_model)
    print(
        f"[train] model params={n_params} (~{n_params / 1000:.1f}K) budget<=200K: {n_params <= 200_000}"
    )
    if n_params > 200_000:
        print(f"[train] WARNING: {n_params} > 200K budget; consider shrinking conv_ch/head_hidden")

    trainable = [p for p in model.parameters() if p.requires_grad]
    if not trainable:
        raise ValueError("没有可训练参数（policy_coef=0 时检查 value_head）")
    opt = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, args.epochs))

    #: 交付 epoch 的**原始** val_loss（文件名/账本口径；B1，2026-11-05）。
    best_val = float("inf")
    #: 选权重用的滑动平均 val_loss（B1，2026-11-07）：单点新低不再当选。
    best_smoothed = float("inf")
    #: 交付权重实际来自哪一（全局）epoch（B1，2026-11-05）：result.json 必须能回答它，
    #: 否则 `history[-1]`（ep80）会被误读成交付精度（2026-11-05 复盘）。
    best_epoch = 0
    best_state: dict[str, torch.Tensor] | None = None
    history: dict[str, list[float]] = {
        "train_loss": [],
        "val_loss": [],
        "move_acc": [],
        "fire_acc": [],
        "value_loss": [],
    }

    t0 = time.time()
    # 全局 epoch 偏移（--epoch-offset）：resume 续训时 ckpt/日志/归档按全局轮编号，
    # 否则第二段 run 的 ckpt.1..N 会撞名第一段（2026-09-09 p3bc 61-90 事故）。
    epoch_base = int(getattr(args, "epoch_offset", 0) or 0)
    epoch_total = epoch_base + args.epochs
    #: `epochs` 是**上限**（B3）：早停后这个数是实际跑完的 epoch 数。
    epochs_done = epoch_base
    patience = int(getattr(args, "patience", 0) or 0)
    for epoch in range(1, args.epochs + 1):
        gepoch = epoch_base + epoch
        model.train()
        run = {"loss": 0.0, "n": 0, "vloss": 0.0, "vn": 0}
        for batch in train_dl:
            if action_v6:
                obs, sc, ex, mv, fr, hd, mm, mf, mh, ret = [b.to(dev) for b in batch]
            else:
                hd = mh = None
                obs, sc, ex, mv, fr, mm, mf, ret = [b.to(dev) for b in batch]
            opt.zero_grad()
            out = model(obs, sc, ex)
            if action_v6:
                # v6 三头：move4 + fire3 + hold2（掩码各段见 dataset.NNDataset）
                lm, lf, lh, vpred = out
                if policy_coef == 0.0:
                    loss = torch.zeros((), device=dev)
                else:
                    policy_loss = (
                        _masked_ce(lm, mv, mm)
                        + _masked_ce(lf, fr, mf, class_weight=fire_class_weight)
                        + _masked_ce(lh, hd, mh, class_weight=hold_class_weight)
                    )
                    loss = policy_loss if policy_coef == 1.0 else policy_coef * policy_loss
                valid = ~torch.isnan(ret)
                if args.value_coef > 0 and valid.any():
                    vloss = F.mse_loss(vpred.squeeze(-1)[valid], ret[valid])
                    loss = loss + args.value_coef * vloss
                    run["vloss"] += float(vloss.item()) * int(valid.sum())
                    run["vn"] += int(valid.sum())
                elif policy_coef == 0.0:
                    raise ValueError(
                        "value-only: batch 内没有有效 return（returns.npy 缺失/全 NaN）"
                    )
            elif use_value:
                lm, lf, vpred = out
                if policy_coef == 0.0:
                    loss = torch.zeros((), device=dev)
                else:
                    policy_loss = _masked_ce(lm, mv, mm) + _masked_ce(
                        lf, fr, mf, class_weight=fire_class_weight
                    )
                    loss = policy_loss if policy_coef == 1.0 else policy_coef * policy_loss
                valid = ~torch.isnan(ret)
                if valid.any():
                    vloss = F.mse_loss(vpred.squeeze(-1)[valid], ret[valid])
                    loss = loss + args.value_coef * vloss
                    run["vloss"] += float(vloss.item()) * int(valid.sum())
                    run["vn"] += int(valid.sum())
                elif policy_coef == 0.0:
                    raise ValueError(
                        "value-only: batch 内没有有效 return（returns.npy 缺失/全 NaN）"
                    )
            else:
                lm, lf = out
                policy_loss = _masked_ce(lm, mv, mm) + _masked_ce(
                    lf, fr, mf, class_weight=fire_class_weight
                )
                loss = policy_loss if policy_coef == 1.0 else policy_coef * policy_loss
            loss.backward()
            # P2-6f：梯度裁剪（与 ppo/engine.py、goal_bc.py 一致，max_norm=1.0）——
            # 此前 bc 无裁剪，坏批次（标注噪声）可一步炸权重。
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            opt.step()
            run["loss"] += loss.item() * obs.shape[0]
            run["n"] += obs.shape[0]
        scheduler.step()
        train_loss = run["loss"] / max(1, run["n"])

        # Validation (no augmentation).
        model.eval()
        v = {
            "loss": 0.0,
            "n": 0,
            "ma": 0.0,
            "fa": 0.0,
            "vloss": 0.0,
            "vn": 0,
            # B2：加权 val 的累加器（v6 才有；与训练目标同口径，仅作诊断）。
            "wloss": 0.0,
            "wn": 0,
        }
        with torch.no_grad():
            for batch in val_dl:
                if action_v6:
                    obs, sc, ex, mv, fr, hd, mm, mf, mh, ret = [b.to(dev) for b in batch]
                else:
                    hd = mh = None
                    obs, sc, ex, mv, fr, mm, mf, ret = [b.to(dev) for b in batch]
                out_v = model(obs, sc, ex)
                if action_v6:
                    lm, lf, lh, vpred = out_v
                elif use_value:
                    lm, lf, vpred = out_v
                else:
                    lm, lf = out_v
                loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf)
                if action_v6:
                    # val_loss 本体**不**施加 class weight（与 `_majority_baseline` 同尺度）；
                    # 加权口径另存 `val_loss_weighted`（B2），避免两条曲线被混读成一个数。
                    loss = loss + _masked_ce(lh, hd, mh)
                    if weight_exp > 0 and (
                        fire_class_weight is not None or hold_class_weight is not None
                    ):
                        wloss = (
                            _masked_ce(lm, mv, mm)
                            + _masked_ce(lf, fr, mf, class_weight=fire_class_weight)
                            + _masked_ce(lh, hd, mh, class_weight=hold_class_weight)
                        )
                        v["wloss"] += wloss.item() * obs.shape[0]
                        v["wn"] += obs.shape[0]
                # val 刻意**不计** fire 正例权重：val_loss 是"真实分布下的 CE"，
                # 要能与 `_majority_baseline`（未加权）同尺度比较；加权只作用于优化目标。
                if use_value:
                    valid = ~torch.isnan(ret)
                    if valid.any():
                        v["vloss"] += float(
                            F.mse_loss(vpred.squeeze(-1)[valid], ret[valid]).item()
                        ) * int(valid.sum())
                        v["vn"] += int(valid.sum())
                v["loss"] += loss.item() * obs.shape[0]
                v["n"] += obs.shape[0]
                v["ma"] += _masked_acc(lm, mv, mm) * obs.shape[0]
                v["fa"] += _masked_acc(lf, fr, mf) * obs.shape[0]
                if action_v6:
                    v["ha"] = v.get("ha", 0.0) + _masked_acc(lh, hd, mh) * obs.shape[0]
        val_loss = v["loss"] / max(1, v["n"])
        ma, fa = v["ma"] / max(1, v["n"]), v["fa"] / max(1, v["n"])  # P2-3：--val-split 0 时 v["n"]==0
        vl = v["vloss"] / max(1, v["vn"]) if v["vn"] > 0 else float("nan")
        history["train_loss"].append(round(train_loss, 4))
        history["val_loss"].append(round(val_loss, 4))
        if v["wn"] > 0:
            history.setdefault("val_loss_weighted", []).append(
                round(v["wloss"] / v["wn"], 4)
            )
        history["move_acc"].append(round(ma, 4))
        history["fire_acc"].append(round(fa, 4))
        #: v6 第三头（2026-11-04）：hold 的 val 准确率。legacy 语料没有该头 ⇒ 不写这条键。
        ha = (v.get("ha", 0.0) / max(1, v["n"])) if action_v6 else None
        if ha is not None:
            history.setdefault("hold_acc", []).append(round(ha, 4))
        history["value_loss"].append(float("nan") if math.isnan(vl) else round(vl, 4))
        print(
            f"[epoch {gepoch:3d}/{epoch_total}] "
            f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
            f"acc move={ma:.3f} fire={fa:.3f} "
            + (f"hold={ha:.3f} " if ha is not None else "")
            + (f"value={vl:.4f} " if not math.isnan(vl) else "")
            + f"lr={opt.param_groups[0]['lr']:.2e}"
        )
        epochs_done = gepoch
        # B1：选权重看**完整窗口**的滑动平均 val_loss，单点新低不再当选（2026-11-07）。
        # 在早停 break 之前判：停的那一轮也可能是平滑曲线的新低（严格 <，无 min_delta）。
        if len(history["val_loss"]) >= SMOOTH_WINDOW:
            smoothed = smoothed_val_loss(history["val_loss"], window=SMOOTH_WINDOW)
            if smoothed < best_smoothed:
                best_smoothed = smoothed
                best_val = val_loss
                best_epoch = gepoch
                best_state = {
                    k: v.detach().clone() for k, v in raw_model.state_dict().items()
                }
        stop_at = early_stop_epoch(history["val_loss"], patience)
        if stop_at is not None and epoch >= stop_at:
            print(
                f"[train] early stop at epoch {gepoch}/{epoch_total}："
                f"滑动平均 val_loss（window={SMOOTH_WINDOW}）连续 {patience} epoch 无改善"
            )
            break
        # Mid-run checkpoint (--ckpt-every N): save current weights (not best, for resume).
        ckpt_every = int(getattr(args, "ckpt_every", 0) or 0)
        if ckpt_every > 0 and epoch % ckpt_every == 0:
            ckpt_path = f"{args.out}.ckpt.{gepoch}"
            save_weights_json(
                raw_model,
                ckpt_path,
                extra_meta={
                    "epoch": gepoch,
                    # 窗口未满时还没有候选权重：写 None，别让 Infinity 进 JSON。
                    "best_val_loss": round(best_val, 4) if best_epoch else None,
                    "ckpt": True,
                },
            )
            print(f"[train] checkpoint epoch {gepoch}/{epoch_total} -> {ckpt_path}")
        # Per-epoch hook（BC 云端回传 2026-09-13）：worker/run_bc 以回调持久化/回传
        # 每 epoch 权重与指标——中断接续训练的落点。传 raw_model（裸键 state_dict）。
        on_epoch = getattr(args, "on_epoch", None)
        if on_epoch is not None:
            on_epoch(
                gepoch,
                raw_model,
                {
                    "train_loss": round(train_loss, 4),
                    "val_loss": round(val_loss, 4),
                    "move_acc": round(ma, 4),
                    "fire_acc": round(fa, 4),
                    # v6 第三头（#11）：以前漏发 ⇒ 账本/面板的 hold 列永远是空。
                    **({"hold_acc": round(ha, 4)} if ha is not None else {}),
                    # B2：加权 val 与 val_loss 并排入账（诊断用，不替代门指标）。
                    **(
                        {"val_loss_weighted": round(v["wloss"] / v["wn"], 4)}
                        if v["wn"] > 0
                        else {}
                    ),
                    "lr": float(opt.param_groups[0]["lr"]),
                },
            )

    if best_state is None:
        if not history["val_loss"]:
            raise RuntimeError("BC 训练没有完成任何 epoch（--epochs 必须 >= 1）")
        # 窗口未满（epochs < SMOOTH_WINDOW）：交最后一轮，而不是退回单点 min。
        best_epoch = epochs_done
        best_val = history["val_loss"][-1]
        best_smoothed = smoothed_val_loss(history["val_loss"], window=SMOOTH_WINDOW)
        best_state = {k: v.detach().clone() for k, v in raw_model.state_dict().items()}

    # Restore best on CPU (weights export/registry must be bitwise-stable
    # regardless of training device).
    model.to("cpu")
    # raw_model 恢复：DP 包装的 state_dict 键带 "module." 前缀，weights.json 必须是裸键
    raw_model.load_state_dict(best_state)
    trained_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "trained_at": trained_at,
        "schema_major": OBS_SCHEMA_MAJOR,
        # on_epoch 回调不可序列化（worker/run_bc 经 SimpleNamespace 注入）——排除出 meta
        "args": {k: v for k, v in vars(args).items() if k != "on_epoch"},
        "sizes": sizes,
        "best_val_loss": round(float(best_val), 4),
        "best_smoothed_val_loss": round(float(best_smoothed), 4),
        "delivered_epoch": int(best_epoch),
        "epochs_completed": int(epochs_done),
        "early_stopped": bool(epochs_done < epoch_total),
        "history": history,
        # 2026-09-14：把"地板"和补偿系数写进 checkpoint —— 只看 fire_acc 会误判
        # （它可能低于"永不发射"的常数基线），必须能随权重回看这两个参照量。
        "majority_ce": {k: _sanitize_json(v) for k, v in mb.items()},
        "fire_pos_weight": float(fire_pos_weight),
    }
    meta = _sanitize_json(meta)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    versioned = os.path.join(
        out_dir, f"weights.{stamp}_ep{epochs_done}_val{float(best_val):.4f}.json"
    )
    save_weights_json(raw_model, versioned, extra_meta=meta)
    _safe_copy(versioned, args.out)  # active pointer for the TS runtime (read-only tolerant)
    _append_weights_md(out_dir, versioned, trained_at, args, sizes, float(best_val), history)
    if args.checkpoint:
        torch.save(
            {"state_dict": raw_model.state_dict(), "arch": raw_model.arch(), "meta": meta}, args.checkpoint
        )
    print(
        f"[train] done in {time.time() - t0:.1f}s -> archive: {versioned} (active: {args.out}) schema_major={OBS_SCHEMA_MAJOR}"
    )
    return {
        "out": versioned,
        "active": args.out,
        "best_val_loss": best_val,
        "best_smoothed_val_loss": best_smoothed,
        "delivered_epoch": int(best_epoch),
        "epochs_completed": int(epochs_done),
        "early_stopped": bool(epochs_done < epoch_total),
        "params": n_params,
        "history": history,
        # 远程 BC job（plan/bc-cloud-integration.plan.md §3）回传 metrics 用；
        # 本地 CLI 消费方忽略此键（行为不变）。
        "sizes": dict(sizes),
        # 2026-09-14：多数类地板 + fire 补偿系数随 metrics 回传（console/账本可见）。
        "majority_ce": {k: _sanitize_json(v) for k, v in mb.items()},
        "fire_pos_weight": float(fire_pos_weight),
        "class_counts": {k: dict(c) for k, c in counts.items()},
    }


def _safe_copy(src: str, dst: str) -> None:
    """Copy ``src`` -> ``dst`` tolerating a read-only destination (common on
    Windows / netdisk-synced files). Clears the read-only bit up front and uses
    an atomic temp-file replace, so we never hit the ``PermissionError`` we saw
    when overwriting ``weights.json`` in place.

    On Windows a held destination (e.g. a reader keeping weights.json open)
    makes os.replace raise WinError 5. We retry with a 30 s budget; if all
    attempts fail the versioned archive is already saved, so we warn and return
    instead of crashing — the round's training is not lost.
    """
    try:
        if os.path.exists(dst):
            os.chmod(dst, 0o666)
    except OSError:
        pass
    tmp = dst + ".tmp"
    shutil.copy(src, tmp)
    for attempt in range(30):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if attempt == 29:
                print(
                    f"[warn] _safe_copy: cannot update {dst} after 30 s; archive {src} is safe",
                    file=sys.stderr,
                )
                return
            time.sleep(1.0)


def main():
    ap = house_parser(description="BC/distillation trainer for NN Player AI")
    ap.add_argument("--data-dir", required=True, help="directory of exported npy shards")
    ap.add_argument(
        "--arch",
        choices=["bc", "student"],
        default="bc",
        help="model architecture: 'bc' = NNPolicy (default), 'student' = "
        "CoordConv-ConvMixer-Lite (plan/RL-Net-Selection.md §4.3; P1.5 distillation)",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "weights.json"),
        help="active weights JSON path (a versioned weights.<stamp>.json archive + WEIGHTS.md are written alongside in the same dir)",
    )
    ap.add_argument(
        "--notes", default="", help="free-text note recorded in WEIGHTS.md for this run"
    )
    ap.add_argument(
        "--resume", default=None, help="resume training from a weights JSON (continue, not retrain)"
    )
    ap.add_argument(
        "--epoch-offset",
        type=int,
        default=0,
        help="global epoch base for --resume continuation (ckpt/log/archive numbering; 0 = fresh run)",
    )
    ap.add_argument(
        "--ckpt-every",
        type=int,
        default=0,
        help=">0: per-N-epoch mid-run checkpoint ({--out}.ckpt.{epoch}) for --resume truncation / mid-run acceptance",
    )
    ap.add_argument("--checkpoint", default=None, help="optional .pt checkpoint path")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument(
        "--patience",
        type=int,
        default=0,
        help="早停：滑动平均 val_loss（window=5）连续 N epoch 无改善就停；0=关（epochs 仍是上限）",
    )
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--mirror-p", type=float, default=0.5, help="mirrorX augmentation prob")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument(
        "--train-heads",
        choices=("all", "policy", "value"),
        default="all",
        help="可训参数范围：all=历史 BC；policy=只训 move/fire/hold 头（trunk 冻结）；"
        "value=只训 value 头（等价 policy_coef=0）",
    )
    ap.add_argument(
        "--fire-pos-weight",
        default="auto",
        help="fire 头 BCE 正例权重：'auto'（按训练集 neg/pos，≈12.6）/ 数字 / 0=关（默认 auto）",
    )
    ap.add_argument(
        "--weight-exponent",
        type=float,
        default=1.0,
        help="v6 class weight 强度：1=完全 inverse-frequency（旧行为）/ 0=不加权 / 中间值 w**exp",
    )
    ap.add_argument(
        "--action-v6",
        action="store_true",
        help="v6 三头语料（a_move/a_fire/a_hold + mask.npy 9 位）；本地 CLI 与云端 manifest 同一开关",
    )
    ap.add_argument(
        "--policy-extra",
        action="store_true",
        help="空间头：POLICY_EXTRA(37) 进 hidden（arch.policyExtra=true）",
    )
    ap.add_argument("--num-workers", type=int, default=0)
    ap.add_argument(
        "--device",
        type=str,
        default="cpu",
        help="torch device for training: cpu | cuda | cuda:N (weights export is always CPU, bits-stable)",
    )
    ap.add_argument(
        "--value-coef",
        type=float,
        default=0.0,
        help=">0: 语料带 returns.npy 时按该系数对 value 头做 MC return 回归 "
        "（M2 ⑥ / M3 value 预置；仅 --arch student 生效）",
    )
    ap.add_argument(
        "--policy-coef",
        type=float,
        default=1.0,
        help="策略 CE 权重；0 = value-only（冻结 trunk/策略头，只训 value_head；需 value-coef>0）",
    )
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    run_main(main)

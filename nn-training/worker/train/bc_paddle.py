"""worker/train/bc_paddle.py — BC 训练器的 Paddle 双生（2026-11-06，plan #10）。

纯 Paddle 机（AI Studio 训练任务）没有 torch；`worker/train/bc.py` 顶层 `import torch`
会让 BC job 在入口就炸。本文件把同一条训练链翻成 Paddle：

  * 数据：`worker.data.npyio.load_dataset` + `shard_split` + `mirror_x`（全是 numpy，
    与 torch 侧同一份切分/镜像判据）；
  * 模型：`worker.models.student_paddle`（键名/形状/权重 JSON 与 torch 双生）；
  * 目标：合法类掩码 CE（非法类 -1e9、单合法类样本跳过）+ v6 三头 + class weight；
  * 优化：AdamW(weight_decay=1e-5) + CosineAnnealing + 梯度裁剪 1.0（与 `bc.py` 同纪律）；
  * 产出：版本化权重 JSON + active 指针；每 epoch 的 `on_epoch` 回调与 torch 同签名。

**双生纪律**：`bc.py` 改任何训练语义（v6 三头 / policy_extra / hold_acc / 续训 / 早停 /
class weight）必须同步改这里；纯判据（早停、fire 权重、NaN 清洗）住 `bc_core.py`，两边
import 同一份，不抄第二份。

只支持 `--arch student`：BC 课程的实际语料都是 student 家族（v6 三头 / policy_extra）；
老 `NNPolicy`（`arch=bc`）没有 Paddle 双生，显式拒绝而不是静默换成另一个模型族。
"""

from __future__ import annotations

import datetime
import os
import random
import shutil
import time
from collections import Counter
from typing import Any

import numpy as np
import paddle
import paddle.nn.functional as F

from common.schema import OBS_SCHEMA_MAJOR
from worker.data.mirror import mirror_x
from worker.data.npyio import load_dataset
from worker.data.shard_split import plan_shard_split, should_split_by_shards
from worker.models.student_paddle import (
    PPOStudent,
    StudentNet,
    load_state_into,
    save_weights_json,
)
from worker.train.bc_core import (
    EARLY_STOP_MIN_DELTA,
    SMOOTH_WINDOW,
    early_stop_epoch,
    resolve_fire_pos_weight,
    sanitize_json,
    smoothed_val_loss,
)

#: 与 torch 侧同一组 torch-only 设备门（Paddle 没有 XLA/DataParallel/MPS 路径）。
_TORCH_ONLY_DEVICES = ("tpu", "xla", "cuda-dp", "dp", "mps")


def _split(
    data: dict[str, np.ndarray], val_split: float, seed: int
) -> tuple[np.ndarray, np.ndarray, dict[str, int], str]:
    """train/val 下标（与 `data/dataset.make_loaders` 同判据）。

    ≥2 个 shard 走整 shard 切分（同局相邻帧不跨集）；单 shard / 旧语料回退样本级。
    随机数用独立 `default_rng(seed)`，不消费全局 RNG。
    """
    n = int(data["obs"].shape[0])
    n_val = int(n * float(val_split))
    rng = np.random.default_rng(seed)
    shard_ids = data.get("shard_ids")
    if should_split_by_shards(shard_ids):
        assert shard_ids is not None
        n_shards = int(shard_ids.max()) + 1
        perm = rng.permutation(n_shards).tolist()
        tr_idx, val_idx = plan_shard_split(shard_ids, n_val, perm)
        sizes = {"train": len(tr_idx), "val": len(val_idx), "total": n}
        return tr_idx, val_idx, sizes, "shard"
    if n < 2:
        raise ValueError(f"语料样本数 {n} < 2——无法切分 train/val")
    n_val = max(0, min(n - 1, n_val))
    perm = rng.permutation(n)
    tr_idx = perm[: n - n_val]
    val_idx = perm[n - n_val :]
    sizes = {"train": len(tr_idx), "val": len(val_idx), "total": n}
    return tr_idx, val_idx, sizes, "sample"


def _class_counts(data: dict[str, np.ndarray], idx: np.ndarray, action_v6: bool) -> dict:
    moves = Counter(int(v) for v in data["actions"][idx, 0])
    fires = Counter(int(v) for v in data["actions"][idx, 1])
    if action_v6:
        holds = Counter(int(v) for v in data["hold"][idx])
        return {"move": moves, "fire": fires, "hold": holds}
    return {"move": moves, "fire": fires}


def _majority_baseline(counts: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, c in counts.items():
        total = sum(c.values())
        out[name] = (
            float("nan") if total == 0 else -float(np.log(max(1, c.most_common(1)[0][1]) / total))
        )
    return out


def _masked_ce(
    logits: paddle.Tensor,
    target: paddle.Tensor,
    mask: paddle.Tensor,
    class_weight: paddle.Tensor | None = None,
) -> paddle.Tensor:
    """`worker/train/bc.py::_masked_ce` 的逐式对应（非法类 -1e9、单合法类跳过）。"""
    keep = mask.sum(axis=-1) >= 2
    if not bool(keep.any().item()):
        return logits.sum() * 0.0
    z = paddle.where(mask > 0, logits, paddle.full_like(logits, -1e9))
    per = F.cross_entropy(
        z[keep], target[keep].astype("int64"), reduction="none", weight=class_weight
    )
    return per.mean()


def _scalar(t: paddle.Tensor) -> float:
    """Tensor → Python float（Paddle 的 `.item()` 返回 `float|bool|complex`，mypy 收窄不了）。"""
    return float(np.asarray(t.numpy()).reshape(-1)[0])


def _masked_acc(
    logits: paddle.Tensor, target: paddle.Tensor, mask: paddle.Tensor
) -> float:
    keep = mask.sum(axis=-1) >= 2
    if not bool(keep.any().item()):
        return 0.0
    z = paddle.where(mask > 0, logits, paddle.full_like(logits, -1e9))
    pred = z.argmax(axis=-1)
    return _scalar((pred[keep] == target[keep].astype("int64")).astype("float32").mean())


def _clip_grad_norm(parameters, max_norm: float) -> paddle.Tensor:
    """torch `clip_grad_norm_(max_norm=1.0)` 的逐式对应。"""
    grads = [p.grad for p in parameters if p.grad is not None]
    if not grads:
        return paddle.zeros([], dtype="float32")
    total = paddle.sqrt(paddle.add_n([g.pow(2).sum() for g in grads]))
    coef = paddle.minimum(max_norm / (total + 1e-6), paddle.ones([], dtype=total.dtype))
    with paddle.no_grad():
        for g in grads:
            g.set_value(g * coef)
    return total


def _make_batch(
    data: dict[str, np.ndarray],
    idx: np.ndarray,
    action_v6: bool,
    rng: np.random.Generator | None,
    mirror_p: float,
):
    """按 torch `NNDataset.__getitem__` 的元组顺序拼一个 batch（numpy → paddle）。"""
    obs: list[np.ndarray] = []
    scalars: list[np.ndarray] = []
    extra: list[np.ndarray] = []
    moves: list[int] = []
    fires: list[int] = []
    holds: list[int] = []
    mask_move: list[np.ndarray] = []
    mask_fire: list[np.ndarray] = []
    mask_hold: list[np.ndarray] = []
    rets: list[float] = []
    returns = data.get("returns")
    for i in idx:
        o = data["obs"][i]
        s = data["scalars"][i]
        e = data["extra"][i]
        mv = int(data["actions"][i, 0])
        fr = int(data["actions"][i, 1])
        if rng is not None and mirror_p > 0 and rng.random() < mirror_p:
            o, s, mv, e = mirror_x(o, s, mv, e, action_v6=action_v6)
        masks = data["masks"][i]
        obs.append(o)
        scalars.append(s)
        extra.append(e)
        moves.append(mv)
        fires.append(fr)
        if action_v6:
            holds.append(int(data["hold"][i]))
            mask_move.append(masks[:4])
            mask_fire.append(masks[4:7])
            mask_hold.append(masks[7:9])
        else:
            mask_move.append(masks[:5])
            mask_fire.append(masks[5:7])
        rets.append(float(returns[i]) if returns is not None else float("nan"))
    t = {
        "obs": paddle.to_tensor(np.stack(obs)),
        "scalars": paddle.to_tensor(np.stack(scalars)),
        "extra": paddle.to_tensor(np.stack(extra)),
        "move": paddle.to_tensor(np.asarray(moves, dtype=np.int64)),
        "fire": paddle.to_tensor(np.asarray(fires, dtype=np.int64)),
        "mask_move": paddle.to_tensor(np.stack(mask_move)),
        "mask_fire": paddle.to_tensor(np.stack(mask_fire)),
        "ret": paddle.to_tensor(np.asarray(rets, dtype=np.float32)),
    }
    if action_v6:
        t["hold"] = paddle.to_tensor(np.asarray(holds, dtype=np.int64))
        t["mask_hold"] = paddle.to_tensor(np.stack(mask_hold))
    return t


def _activate(src: str, dst: str) -> None:
    """active 指针原子替换（崩溃不会留下半个文件）。"""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def train(args: Any) -> dict[str, Any]:
    """Paddle BC 训练。参数面与 `bc.py::train(SimpleNamespace)` 对齐。"""
    arch = str(getattr(args, "arch", "student"))
    if arch != "student":
        raise ValueError(
            f"Paddle BC 只支持 --arch student（收到 {arch!r}）；"
            "老 NNPolicy（arch=bc）没有 Paddle 双生，拒绝静默换模型族"
        )
    if getattr(args, "checkpoint", None):
        raise ValueError(
            "Paddle BC 不支持 --checkpoint（torch .pt 格式）；"
            "请用 --out 权重 JSON / --ckpt-every 归档，或改用 torch 后端"
        )

    seed = int(args.seed)
    paddle.seed(seed)
    np.random.seed(seed)
    random.seed(seed)

    action_v6 = bool(getattr(args, "action_v6", False))
    policy_extra = bool(getattr(args, "policy_extra", False))
    data = load_dataset(str(args.data_dir), action_v6=action_v6)
    tr_idx, val_idx, sizes, split_kind = _split(data, float(args.val_split), seed)
    print(
        f"[train] samples total={sizes['total']} train={sizes['train']} val={sizes['val']}"
        f" split={split_kind}"
    )

    dev = str(getattr(args, "device", "cpu") or "cpu").strip().lower()
    if dev in _TORCH_ONLY_DEVICES:
        raise ValueError(f"paddle 后端不支持 --device {dev!r}（torch 专属实验臂）")
    if dev == "auto":
        dev = (
            "cuda"
            if bool(paddle.is_compiled_with_cuda())
            and int(paddle.device.cuda.device_count()) > 0
            else "cpu"
        )
    paddle.set_device(dev)
    print(f"[train] backend=paddle device={paddle.get_device()}")

    use_value = (float(getattr(args, "value_coef", 0.0) or 0.0) > 0 or action_v6)
    model: paddle.nn.Layer = (
        PPOStudent(action_v6=action_v6, policy_extra=policy_extra)
        if use_value
        else StudentNet(action_v6=action_v6, policy_extra=policy_extra)
    )
    if getattr(args, "resume", None):
        print(f"[train] resuming from {args.resume}")
        missing = load_state_into(model, str(args.resume))
        if missing:
            print(f"[train] WARNING: resume 未装载 {len(missing)} 个键")
    n_params = sum(int(p.numel()) for p in model.parameters())
    print(
        f"[train] model params={n_params} (~{n_params / 1000:.1f}K) budget<=200K: "
        f"{n_params <= 200_000}"
    )

    counts = _class_counts(data, tr_idx, action_v6)
    mb = _majority_baseline(counts)
    tail = f" hold={mb['hold']:.4f}" if action_v6 else ""
    print(f"[train] majority-baseline CE: move={mb['move']:.4f} fire={mb['fire']:.4f}{tail}")

    weight_exp = float(getattr(args, "weight_exponent", 1.0) or 0.0)
    if weight_exp < 0:
        raise ValueError(f"weight_exponent 必须 ≥ 0（得到 {weight_exp}）")
    fire_class_weight: paddle.Tensor | None = None
    hold_class_weight: paddle.Tensor | None = None
    if action_v6:
        cf = counts["fire"]
        ftot = sum(cf.values()) or 1
        fw = [((ftot / (3 * max(1, cf.get(k, 0)))) ** weight_exp) for k in range(3)]
        fire_class_weight = (
            paddle.to_tensor(fw, dtype="float32") if weight_exp > 0 else None
        )
        ch = counts["hold"]
        htot = sum(ch.values()) or 1
        hw = [((htot / (2 * max(1, ch.get(k, 0)))) ** weight_exp) for k in range(2)]
        hold_class_weight = (
            paddle.to_tensor(hw, dtype="float32") if weight_exp > 0 else None
        )
        fire_pos_weight = -1.0
    else:
        fire_pos_weight = resolve_fire_pos_weight(
            getattr(args, "fire_pos_weight", 0.0), counts
        )
        fire_class_weight = (
            paddle.to_tensor([1.0, float(fire_pos_weight)], dtype="float32")
            if fire_pos_weight > 0
            else None
        )
    print(
        f"[train] fire_pos_weight={fire_pos_weight:.2f}"
        f"{'（正例加权开启）' if fire_class_weight is not None else '（关闭）'}"
        f" fire counts neg={counts['fire'].get(0, 0)} pos={counts['fire'].get(1, 0)}"
    )
    if action_v6:
        print(f"[train] weight_exponent={weight_exp:.2f}（0=不加权 / 1=完全 inverse-freq）")

    scheduler = paddle.optimizer.lr.CosineAnnealingDecay(
        learning_rate=float(args.lr), T_max=max(1, int(args.epochs))
    )
    opt = paddle.optimizer.AdamW(
        learning_rate=scheduler,
        parameters=model.parameters(),
        weight_decay=1e-5,
    )

    history: dict[str, list[float]] = {
        "train_loss": [],
        "val_loss": [],
        "move_acc": [],
        "fire_acc": [],
        "value_loss": [],
    }
    #: 交付 epoch 的**原始** val_loss（文件名/账本口径）；选权重看 best_smoothed。
    best_val = float("inf")
    best_smoothed = float("inf")
    best_epoch = 0
    best_state: dict[str, paddle.Tensor] | None = None
    epoch_base = int(getattr(args, "epoch_offset", 0) or 0)
    epoch_total = epoch_base + int(args.epochs)
    patience = int(getattr(args, "patience", 0) or 0)
    epochs_done = epoch_base
    rng = np.random.default_rng(seed + 1)
    t0 = time.time()

    for epoch in range(1, int(args.epochs) + 1):
        gepoch = epoch_base + epoch
        model.train()
        order = rng.permutation(len(tr_idx))
        run = {"loss": 0.0, "n": 0, "vloss": 0.0, "vn": 0}
        for start in range(0, len(order), int(args.batch)):
            sel = tr_idx[order[start : start + int(args.batch)]]
            b = _make_batch(
                data, sel, action_v6, rng, float(getattr(args, "mirror_p", 0.5))
            )
            opt.clear_grad()
            out = model(b["obs"], b["scalars"], b["extra"])
            if action_v6:
                lm, lf, lh, vpred = out
                loss = (
                    _masked_ce(lm, b["move"], b["mask_move"])
                    + _masked_ce(lf, b["fire"], b["mask_fire"], class_weight=fire_class_weight)
                    + _masked_ce(lh, b["hold"], b["mask_hold"], class_weight=hold_class_weight)
                )
                valid = ~paddle.isnan(b["ret"])
                if float(getattr(args, "value_coef", 0.0) or 0.0) > 0 and bool(
                    valid.any().item()
                ):
                    vloss = F.mse_loss(vpred.squeeze(-1)[valid], b["ret"][valid])
                    loss = loss + float(args.value_coef) * vloss
                    nv = int(_scalar(valid.astype("int64").sum()))
                    run["vloss"] += _scalar(vloss) * nv
                    run["vn"] += nv
            elif use_value:
                lm, lf, vpred = out
                loss = _masked_ce(lm, b["move"], b["mask_move"]) + _masked_ce(
                    lf, b["fire"], b["mask_fire"], class_weight=fire_class_weight
                )
                valid = ~paddle.isnan(b["ret"])
                if bool(valid.any().item()):
                    vloss = F.mse_loss(vpred.squeeze(-1)[valid], b["ret"][valid])
                    loss = loss + float(args.value_coef) * vloss
                    nv = int(_scalar(valid.astype("int64").sum()))
                    run["vloss"] += _scalar(vloss) * nv
                    run["vn"] += nv
            else:
                lm, lf = out
                loss = _masked_ce(lm, b["move"], b["mask_move"]) + _masked_ce(
                    lf, b["fire"], b["mask_fire"], class_weight=fire_class_weight
                )
            loss.backward()
            _clip_grad_norm(model.parameters(), 1.0)
            opt.step()
            run["loss"] += _scalar(loss) * int(b["obs"].shape[0])
            run["n"] += int(b["obs"].shape[0])
        scheduler.step()
        train_loss = run["loss"] / max(1, run["n"])

        model.eval()
        v = {
            "loss": 0.0,
            "n": 0,
            "ma": 0.0,
            "fa": 0.0,
            "ha": 0.0,
            "vloss": 0.0,
            "vn": 0,
            "wloss": 0.0,
            "wn": 0,
        }
        with paddle.no_grad():
            for start in range(0, len(val_idx), int(args.batch)):
                sel = val_idx[start : start + int(args.batch)]
                b = _make_batch(data, sel, action_v6, None, 0.0)
                out_v = model(b["obs"], b["scalars"], b["extra"])
                if action_v6:
                    lm, lf, lh, vpred = out_v
                elif use_value:
                    lm, lf, vpred = out_v
                else:
                    lm, lf = out_v
                loss = _masked_ce(lm, b["move"], b["mask_move"]) + _masked_ce(
                    lf, b["fire"], b["mask_fire"]
                )
                if action_v6:
                    loss = loss + _masked_ce(lh, b["hold"], b["mask_hold"])
                    if weight_exp > 0 and (
                        fire_class_weight is not None or hold_class_weight is not None
                    ):
                        wloss = (
                            _masked_ce(lm, b["move"], b["mask_move"])
                            + _masked_ce(
                                lf,
                                b["fire"],
                                b["mask_fire"],
                                class_weight=fire_class_weight,
                            )
                            + _masked_ce(
                                lh,
                                b["hold"],
                                b["mask_hold"],
                                class_weight=hold_class_weight,
                            )
                        )
                        v["wloss"] += _scalar(wloss) * int(b["obs"].shape[0])
                        v["wn"] += int(b["obs"].shape[0])
                if use_value:
                    valid = ~paddle.isnan(b["ret"])
                    if bool(valid.any().item()):
                        vloss = F.mse_loss(vpred.squeeze(-1)[valid], b["ret"][valid])
                        nv = int(_scalar(valid.astype("int64").sum()))
                        v["vloss"] += _scalar(vloss) * nv
                        v["vn"] += nv
                n = int(b["obs"].shape[0])
                v["loss"] += _scalar(loss) * n
                v["n"] += n
                v["ma"] += _masked_acc(lm, b["move"], b["mask_move"]) * n
                v["fa"] += _masked_acc(lf, b["fire"], b["mask_fire"]) * n
                if action_v6:
                    v["ha"] += _masked_acc(lh, b["hold"], b["mask_hold"]) * n
        val_loss = v["loss"] / max(1, v["n"])
        ma = v["ma"] / max(1, v["n"])
        fa = v["fa"] / max(1, v["n"])
        ha = (v["ha"] / max(1, v["n"])) if action_v6 else None
        vl = v["vloss"] / max(1, v["vn"]) if v["vn"] > 0 else float("nan")
        history["train_loss"].append(round(train_loss, 4))
        history["val_loss"].append(round(val_loss, 4))
        if v["wn"] > 0:
            history.setdefault("val_loss_weighted", []).append(
                round(v["wloss"] / v["wn"], 4)
            )
        history["move_acc"].append(round(ma, 4))
        history["fire_acc"].append(round(fa, 4))
        if ha is not None:
            history.setdefault("hold_acc", []).append(round(ha, 4))
        history["value_loss"].append(float("nan") if vl != vl else round(vl, 4))
        print(
            f"[epoch {gepoch:3d}/{epoch_total}] "
            f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
            f"acc move={ma:.3f} fire={fa:.3f} "
            + (f"hold={ha:.3f} " if ha is not None else "")
            + (f"value={vl:.4f} " if vl == vl else "")
            + f"lr={float(opt.get_lr()):.2e}"
        )
        epochs_done = gepoch
        # B1：与 bc.py 同一判据——完整窗口的滑动平均 val_loss，单点新低不当选。
        if len(history["val_loss"]) >= SMOOTH_WINDOW:
            smoothed = smoothed_val_loss(history["val_loss"], window=SMOOTH_WINDOW)
            if smoothed < best_smoothed:
                best_smoothed = smoothed
                best_val = val_loss
                best_epoch = gepoch
                best_state = {k: t.clone() for k, t in model.state_dict().items()}
        stop_at = early_stop_epoch(history["val_loss"], patience)
        if stop_at is not None and epoch >= stop_at:
            print(
                f"[train] early stop at epoch {gepoch}/{epoch_total}："
                f"滑动平均 val_loss（window={SMOOTH_WINDOW}）连续 {patience} epoch 无改善"
            )
            break
        ckpt_every = int(getattr(args, "ckpt_every", 0) or 0)
        if ckpt_every > 0 and epoch % ckpt_every == 0:
            ckpt_path = f"{args.out}.ckpt.{gepoch}"
            save_weights_json(
                model,
                ckpt_path,
                extra_meta={
                    "epoch": gepoch,
                    # 窗口未满时还没有候选权重：写 None，别让 Infinity 进 JSON。
                    "best_val_loss": round(best_val, 4) if best_epoch else None,
                    "ckpt": True,
                },
            )
            print(f"[train] checkpoint epoch {gepoch}/{epoch_total} -> {ckpt_path}")
        on_epoch = getattr(args, "on_epoch", None)
        if on_epoch is not None:
            on_epoch(
                gepoch,
                model,
                {
                    "train_loss": round(train_loss, 4),
                    "val_loss": round(val_loss, 4),
                    "move_acc": round(ma, 4),
                    "fire_acc": round(fa, 4),
                    **({"hold_acc": round(ha, 4)} if ha is not None else {}),
                    **(
                        {"val_loss_weighted": round(v["wloss"] / v["wn"], 4)}
                        if v["wn"] > 0
                        else {}
                    ),
                    "lr": float(opt.get_lr()),
                },
            )

    if best_state is None:
        if not history["val_loss"]:
            raise RuntimeError("BC 训练没有完成任何 epoch（--epochs 必须 >= 1）")
        # 窗口未满（epochs < SMOOTH_WINDOW）：交最后一轮，而不是退回单点 min。
        best_epoch = epochs_done
        best_val = history["val_loss"][-1]
        best_smoothed = smoothed_val_loss(history["val_loss"], window=SMOOTH_WINDOW)
        best_state = {k: t.clone() for k, t in model.state_dict().items()}
    model.set_state_dict(best_state)
    trained_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "trained_at": trained_at,
        "schema_major": OBS_SCHEMA_MAJOR,
        "args": {k: v for k, v in vars(args).items() if k != "on_epoch"},
        "sizes": sizes,
        "best_val_loss": round(float(best_val), 4),
        "best_smoothed_val_loss": round(float(best_smoothed), 4),
        "delivered_epoch": int(best_epoch),
        "epochs_completed": int(epochs_done),
        "early_stopped": bool(epochs_done < epoch_total),
        "history": history,
        "majority_ce": {k: sanitize_json(v) for k, v in mb.items()},
        "fire_pos_weight": float(fire_pos_weight),
        "backend": "paddle",
    }
    meta = sanitize_json(meta)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    versioned = os.path.join(
        out_dir, f"weights.{stamp}_ep{epochs_done}_val{float(best_val):.4f}.json"
    )
    save_weights_json(model, versioned, extra_meta=meta)
    _activate(versioned, str(args.out))
    print(
        f"[train] done in {time.time() - t0:.1f}s -> archive: {versioned} "
        f"(active: {args.out}) schema_major={OBS_SCHEMA_MAJOR} backend=paddle"
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
        "sizes": dict(sizes),
        "majority_ce": {k: sanitize_json(v) for k, v in mb.items()},
        "fire_pos_weight": float(fire_pos_weight),
        "class_counts": {k: {int(kk): int(vv) for kk, vv in c.items()} for k, c in counts.items()},
    }


# `bc_core.smoothed_val_loss` / `EARLY_STOP_MIN_DELTA` 是给对账脚本与测试的转发面；
# 训练循环只用 early_stop_epoch。保留 import 让名字在本模块可见（与 bc.py 同约定）。
__all__ = [
    "EARLY_STOP_MIN_DELTA",
    "SMOOTH_WINDOW",
    "smoothed_val_loss",
    "train",
]

"""
Behavior-cloning trainer (plan §NN-M1).

Consumes exported npy shards (obs / scalars / actions / masks / conditions) and
trains the 3-headed policy with masked cross-entropy:
  * Only samples where the invalid-action mask == 1 contribute to a head's loss
    (plan §1.3-4 — turn-locked / cooldown / zero-stock actions are masked out).
  * mirrorX online augmentation (prob --mirror-p) on the training split only.
  * CPU-only (plan: 8-core 32G, no GPU). Small network (~77K params) matches
    the 40-120K BC sample count (underfit-safe).

Outputs:
  * <--out>  : JSON+base64 weights (plan §5), loadable by the TS runtime.
  * <--out>.pt : torch checkpoint (Python-side convenience).
"""
from __future__ import annotations

import argparse
import datetime
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter

import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset import make_loaders  # noqa: E402
from model import NNPolicy, param_count  # noqa: E402
from student_model import StudentNet, PPOStudent  # noqa: E402
from weights_io import save_weights_json, load_state_into  # noqa: E402
from schema import OBS_SCHEMA_MAJOR  # noqa: E402


def _masked_ce(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Mean CE over samples where mask==1 (mask: (B, K))."""
    per = F.cross_entropy(logits, target, reduction="none")  # (B,)
    m = mask.sum(dim=-1)  # (B,) — at least one valid class per sample
    loss = (per * m) / m.clamp(min=1)
    return loss.mean()


def _masked_acc(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    pred = logits.argmax(dim=-1)
    correct = (pred == target).float() * mask.sum(dim=-1)
    denom = mask.sum(dim=-1).clamp(min=1)
    return (correct / denom).mean().item()


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

| trained_at | file | epochs | samples (train/val) | val_loss | move/fire/item acc | git | notes |
|---|---|---|---|---|---|---|---|
"""


def _git_sha() -> str:
    """Best-effort short git sha of the repo at training time (for the registry)."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "n/a"


def _append_weights_md(out_dir: str, versioned_path: str, trained_at: str,
                       args, sizes: dict, best_val: float, history: dict) -> None:
    """Append a row to the committed weights registry (WEIGHTS.md).

    Creates the file with a header (naming convention + backup strategy) on first run.
    """
    md_path = os.path.join(out_dir, "WEIGHTS.md")
    row = (
        f"| {trained_at} | `{os.path.basename(versioned_path)}` | {args.epochs} "
        f"| {sizes['train']}/{sizes['val']} | {best_val:.4f} "
        f"| {history['move_acc'][-1]}/{history['fire_acc'][-1]}/{history['value_loss'][-1]} "
        f"| {_git_sha()} | {args.notes} |\n"
    )
    if not os.path.exists(md_path):
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_WEIGHTS_MD_HEADER)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(row)


def _majority_baseline(dl) -> dict:
    """CE if we always predict the most frequent class per head.

    Provides a floor to interpret the trained val_loss against (audit gap #2):
    is 1.2431 near the majority-class ceiling, or is there real signal learned?
    """
    c_m, c_f = Counter(), Counter()
    for batch in dl:
        obs, sc, mv, fr, mm, mf, ret = [b for b in batch]
        for t, c in ((mv, c_m), (fr, c_f)):
            for v in t.tolist():
                c[v] += 1
    out = {}
    for name, c in (("move", c_m), ("fire", c_f)):
        total = sum(c.values())
        if total == 0:
            out[name] = float("nan")
            continue
        maj = c.most_common(1)[0][1]
        out[name] = -math.log(maj / total)  # CE of constant majority prediction
    return out


def train(args) -> dict:
    torch.manual_seed(args.seed)
    train_dl, val_dl, sizes = make_loaders(
        args.data_dir, args.batch, args.val_split, args.mirror_p, args.seed, args.num_workers
    )
    print(f"[train] samples total={sizes['total']} train={sizes['train']} val={sizes['val']}")

    # Majority-class baseline: a floor to interpret val_loss against (audit gap #2).
    mb = _majority_baseline(train_dl)
    print(f"[train] majority-baseline CE: move={mb['move']:.4f} fire={mb['fire']:.4f}")

    # v2（M3）：语料带 returns.npy 且 --arch student 时构建 PPOStudent，按
    # `--value-coef` 把 MC return 作为 value 头回归目标（M2 ⑥ 的 value MC 预置）。
    # 纯 BC（无 returns）沿用 StudentNet / NNPolicy 双头。
    use_value = getattr(args, "value_coef", 0.0) > 0 and args.arch == "student"
    model = PPOStudent() if use_value else (StudentNet() if args.arch == "student" else NNPolicy())  # type: ignore
    if getattr(args, "resume", None):
        print(f"[train] resuming from {args.resume}")
        load_state_into(model, args.resume)
    n_params = param_count(model)
    print(f"[train] model params={n_params} (~{n_params/1000:.1f}K) budget<=200K: {n_params <= 200_000}")
    if n_params > 200_000:
        print(f"[train] WARNING: {n_params} > 200K budget; consider shrinking conv_ch/head_hidden")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, args.epochs))

    best_val = float("inf")
    history = {"train_loss": [], "val_loss": [], "move_acc": [], "fire_acc": [], "value_loss": []}

    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        run = {"loss": 0.0, "n": 0, "vloss": 0.0, "vn": 0}
        for batch in train_dl:
            obs, sc, mv, fr, mm, mf, ret = [b for b in batch]
            opt.zero_grad()
            if use_value:
                lm, lf, vpred = model(obs, sc)
                loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf)
                valid = ~torch.isnan(ret)
                if valid.any():
                    vloss = F.mse_loss(vpred.squeeze(-1)[valid], ret[valid])
                    loss = loss + args.value_coef * vloss
                    run["vloss"] += float(vloss.item()) * int(valid.sum())
                    run["vn"] += int(valid.sum())
            else:
                lm, lf = model(obs, sc)
                loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf)
            loss.backward()
            opt.step()
            run["loss"] += loss.item() * obs.shape[0]
            run["n"] += obs.shape[0]
        scheduler.step()
        train_loss = run["loss"] / max(1, run["n"])

        # Validation (no augmentation).
        model.eval()
        v = {"loss": 0.0, "n": 0, "ma": 0.0, "fa": 0.0, "vloss": 0.0, "vn": 0}
        with torch.no_grad():
            for batch in val_dl:
                obs, sc, mv, fr, mm, mf, ret = [b for b in batch]
                if use_value:
                    lm, lf, vpred = model(obs, sc)
                else:
                    lm, lf = model(obs, sc)
                loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf)
                if use_value:
                    valid = ~torch.isnan(ret)
                    if valid.any():
                        v["vloss"] += float(F.mse_loss(vpred.squeeze(-1)[valid], ret[valid]).item()) * int(valid.sum())
                        v["vn"] += int(valid.sum())
                v["loss"] += loss.item() * obs.shape[0]
                v["n"] += obs.shape[0]
                v["ma"] += _masked_acc(lm, mv, mm) * obs.shape[0]
                v["fa"] += _masked_acc(lf, fr, mf) * obs.shape[0]
        val_loss = v["loss"] / max(1, v["n"])
        ma, fa = v["ma"] / v["n"], v["fa"] / v["n"]
        vl = v["vloss"] / max(1, v["vn"]) if v["vn"] > 0 else float("nan")
        history["train_loss"].append(round(train_loss, 4))
        history["val_loss"].append(round(val_loss, 4))
        history["move_acc"].append(round(ma, 4))
        history["fire_acc"].append(round(fa, 4))
        history["value_loss"].append(float("nan") if math.isnan(vl) else round(vl, 4))
        print(f"[epoch {epoch:3d}/{args.epochs}] "
              f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
              f"acc move={ma:.3f} fire={fa:.3f} "
              + (f"value={vl:.4f} " if not math.isnan(vl) else "")
              + f"lr={opt.param_groups[0]['lr']:.2e}")
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    # Restore best and export (versioned archive + active pointer + history md).
    model.load_state_dict(best_state)
    trained_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "trained_at": trained_at,
        "schema_major": OBS_SCHEMA_MAJOR,
        "args": vars(args),
        "sizes": sizes,
        "best_val_loss": round(float(best_val), 4),
        "history": history,
    }
    out_dir = os.path.dirname(os.path.abspath(args.out))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    versioned = os.path.join(out_dir, f"weights.{stamp}_ep{args.epochs}_val{float(best_val):.4f}.json")
    save_weights_json(model, versioned, extra_meta=meta)
    _safe_copy(versioned, args.out)  # active pointer for the TS runtime (read-only tolerant)
    _append_weights_md(out_dir, versioned, trained_at, args, sizes, float(best_val), history)
    if args.checkpoint:
        torch.save({"state_dict": model.state_dict(), "arch": model.arch(), "meta": meta},
                   args.checkpoint)
    print(f"[train] done in {time.time()-t0:.1f}s -> archive: {versioned} (active: {args.out}) schema_major={OBS_SCHEMA_MAJOR}")
    return {"out": versioned, "active": args.out, "best_val_loss": best_val, "params": n_params, "history": history}


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
                    f"[warn] _safe_copy: cannot update {dst} after 30 s; "
                    f"archive {src} is safe",
                    file=sys.stderr,
                )
                return
            time.sleep(1.0)


def main():
    ap = argparse.ArgumentParser(description="BC/distillation trainer for NN Player AI")
    ap.add_argument("--data-dir", required=True, help="directory of exported npy shards")
    ap.add_argument("--arch", choices=["bc", "student"], default="bc",
                    help="model architecture: 'bc' = NNPolicy (default), 'student' = "
                         "CoordConv-ConvMixer-Lite (plan/RL-Net-Selection.md §4.3; P1.5 distillation)")
    ap.add_argument("--out",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "weights.json"),
                    help="active weights JSON path (a versioned weights.<stamp>.json archive + WEIGHTS.md are written alongside in the same dir)")
    ap.add_argument("--notes", default="", help="free-text note recorded in WEIGHTS.md for this run")
    ap.add_argument("--resume", default=None, help="resume training from a weights JSON (continue, not retrain)")
    ap.add_argument("--checkpoint", default=None, help="optional .pt checkpoint path")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--mirror-p", type=float, default=0.5, help="mirrorX augmentation prob")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--num-workers", type=int, default=0)
    ap.add_argument("--value-coef", type=float, default=0.0,
                    help=">0: 语料带 returns.npy 时按该系数对 value 头做 MC return 回归 "
                         "（M2 ⑥ / M3 value 预置；仅 --arch student 生效）")
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    main()

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
import os
import sys
import time

import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset import make_loaders  # noqa: E402
from model import NNPolicy, param_count  # noqa: E402
from weights_io import save_weights_json  # noqa: E402
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


def train(args) -> dict:
    torch.manual_seed(args.seed)
    train_dl, val_dl, sizes = make_loaders(
        args.data_dir, args.batch, args.val_split, args.mirror_p, args.seed, args.num_workers
    )
    print(f"[train] samples total={sizes['total']} train={sizes['train']} val={sizes['val']}")

    model = NNPolicy()
    n_params = param_count(model)
    print(f"[train] NNPolicy params={n_params} (~{n_params/1000:.1f}K) budget<=200K: {n_params <= 200_000}")
    if n_params > 200_000:
        print(f"[train] WARNING: {n_params} > 200K budget; consider shrinking conv_ch/head_hidden")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, args.epochs))

    best_val = float("inf")
    history = {"train_loss": [], "val_loss": [], "move_acc": [], "fire_acc": [], "item_acc": []}

    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        run = {"loss": 0.0, "n": 0}
        for batch in train_dl:
            obs, sc, mv, fr, it, mm, mf, mi = [b for b in batch]
            opt.zero_grad()
            lm, lf, li = model(obs, sc)
            loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf) + _masked_ce(li, it, mi)
            loss.backward()
            opt.step()
            run["loss"] += loss.item() * obs.shape[0]
            run["n"] += obs.shape[0]
        scheduler.step()
        train_loss = run["loss"] / max(1, run["n"])

        # Validation (no augmentation).
        model.eval()
        v = {"loss": 0.0, "n": 0, "ma": 0.0, "fa": 0.0, "ia": 0.0}
        with torch.no_grad():
            for batch in val_dl:
                obs, sc, mv, fr, it, mm, mf, mi = [b for b in batch]
                lm, lf, li = model(obs, sc)
                loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf) + _masked_ce(li, it, mi)
                v["loss"] += loss.item() * obs.shape[0]
                v["n"] += obs.shape[0]
                v["ma"] += _masked_acc(lm, mv, mm) * obs.shape[0]
                v["fa"] += _masked_acc(lf, fr, mf) * obs.shape[0]
                v["ia"] += _masked_acc(li, it, mi) * obs.shape[0]
        val_loss = v["loss"] / max(1, v["n"])
        ma, fa, ia = v["ma"] / v["n"], v["fa"] / v["n"], v["ia"] / v["n"]
        history["train_loss"].append(round(train_loss, 4))
        history["val_loss"].append(round(val_loss, 4))
        history["move_acc"].append(round(ma, 4))
        history["fire_acc"].append(round(fa, 4))
        history["item_acc"].append(round(ia, 4))
        print(f"[epoch {epoch:3d}/{args.epochs}] "
              f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
              f"acc move={ma:.3f} fire={fa:.3f} item={ia:.3f} "
              f"lr={opt.param_groups[0]['lr']:.2e}")
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    # Restore best and export.
    model.load_state_dict(best_state)
    meta = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema_major": OBS_SCHEMA_MAJOR,
        "args": vars(args),
        "sizes": sizes,
        "best_val_loss": round(float(best_val), 4),
        "history": history,
    }
    save_weights_json(model, args.out, extra_meta=meta)
    if args.checkpoint:
        torch.save({"state_dict": model.state_dict(), "arch": model.arch(), "meta": meta},
                   args.checkpoint)
    print(f"[train] done in {time.time()-t0:.1f}s -> weights: {args.out} (schema_major={OBS_SCHEMA_MAJOR})")
    return {"out": args.out, "best_val_loss": best_val, "params": n_params, "history": history}


def main():
    ap = argparse.ArgumentParser(description="BC trainer for NN Player AI")
    ap.add_argument("--data-dir", required=True, help="directory of exported npy shards")
    ap.add_argument("--out", required=True, help="output weights JSON path")
    ap.add_argument("--checkpoint", default=None, help="optional .pt checkpoint path")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--mirror-p", type=float, default=0.5, help="mirrorX augmentation prob")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--num-workers", type=int, default=0)
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    main()

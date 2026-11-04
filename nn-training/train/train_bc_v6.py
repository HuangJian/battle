"""train_bc_v6.py —— 从 `export-rl-rollout` 的 **v6 原生 shard** 训 BC。

为什么单独一个脚本（不接 `kind: bc`）：
  现有 BC 管道（`remote/bc_job.py:328`）的语料是 **legacy 2 头**（`actions/2 u1` = move+fire），
  而 v6 是**三头**（move4 / fire3 / hold2）。`export-rl-rollout` 落的正是 v6 三头
  （`a_move.npy / a_fire.npy / a_hold.npy`）+ `obs/scalars/extra` + `metrics.npy`
  ⇒ 这里只做「读 shard → 三头 CE → 存权重」，**不碰 `kind: bc` 的现有契约**。

用法：
  bash tools/githook/nn-py-safe.sh nn-training/train/train_bc_v6.py \
    --data-dir tmp/bc-corpus \
    --init-weights nn-training/weights/z7-maze/z7-maze.it0.json \
    --out nn-training/weights/bc-v6/bc-v6.ep60.json \
    --epochs 60 --batch 256 --lr 3e-4 --outcome stage_clear

语料要求（每局一个 shard 目录）：
  manifest.json · obs.npy [N,17,26,26] u8 · scalars.npy [N,46] f4 · extra.npy [N,9] f4
  · a_move.npy [N] u8 · a_fire.npy [N] u8 · a_hold.npy [N] u8 · metrics.npy [N,90] f8

设计要点（都来自历史判决）：
  · **行为门不是 val acc**（§47：val 0.735 健康但 held-out 0.00）⇒ 本脚本只报 train/val 的
    逐头准确率与**动作熵**，胜负必须用 `eval-course-ckpt.ts` 单独跑。
  · **`value_coef = 0`**（`bc-c4-v3` 先例）：value 尺度未标定，BC 只训 policy，
    value 头留给之后的 PPO 现训。
  · **`fire` 的类别不平衡**：v6 的 fire 是 3 类，绝大多数是 0（不射）⇒ 用 inverse-frequency
    class weight，否则该头退化成「永不发射」。
  · **obs 不除 255**：`student.py:175` 已经把 stem 权重乘了 1/255 ⇒ 输入必须是 0..255 量级。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nn-training"))

import torch
import torch.nn.functional as F

from worker.data.weights_io import load_state_into, save_weights_json
from worker.models.arch_meta import read_arch_kwargs
from worker.models.student import PPOStudent


def log(f, *a):
    print(*a, file=f, flush=True)


def load_shards(data_dir: str, outcome: str, max_games: int, log_f) -> list[dict]:
    """读所有 shard；按 `manifest.outcome` 过滤；返回 [{obs, scal, extra, am, af, ah}]。"""
    out = []
    for mp in sorted(glob.glob(os.path.join(data_dir, "**", "manifest.json"), recursive=True)):
        d = os.path.dirname(mp)
        try:
            man = json.load(open(mp, encoding="utf-8"))
        except Exception as e:
            log(log_f, "  [skip] manifest 读不了:", mp, e)
            continue
        oc = str(man.get("outcome") or "")
        if outcome and oc != outcome:
            continue
        need = ("obs.npy", "scalars.npy", "a_move.npy")
        if not all(os.path.exists(os.path.join(d, n)) for n in need):
            log(log_f, "  [skip] 缺 npy:", d)
            continue
        obs = np.load(os.path.join(d, "obs.npy"))
        scal = np.load(os.path.join(d, "scalars.npy"))
        am = np.load(os.path.join(d, "a_move.npy")).astype(np.int64)
        # obs 可能比动作多一行（终局快照）⇒ 按动作长度截断
        n = min(len(obs), len(scal), len(am))
        obs, scal, am = obs[:n], scal[:n], am[:n]
        extra_p = os.path.join(d, "extra.npy")
        extra = np.load(extra_p)[:n] if os.path.exists(extra_p) else np.zeros((n, 1), np.float32)
        af_p, ah_p = os.path.join(d, "a_fire.npy"), os.path.join(d, "a_hold.npy")
        af = np.load(af_p)[:n].astype(np.int64) if os.path.exists(af_p) else np.zeros(n, np.int64)
        ah = np.load(ah_p)[:n].astype(np.int64) if os.path.exists(ah_p) else np.zeros(n, np.int64)
        out.append({"dir": d, "outcome": oc, "obs": obs, "scal": scal, "extra": extra,
                    "am": am, "af": af, "ah": ah})
        if max_games and len(out) >= max_games:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--init-weights", required=True, help="提供 arch 与初始权重（v7 三头）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--outcome", default="stage_clear", help="只收该结局；空串 = 全收")
    ap.add_argument("--max-games", type=int, default=0, help="0 = 不限（按内存自控）")
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--log", default="")
    a = ap.parse_args()

    _logp = a.log or (a.out + ".log")
    os.makedirs(os.path.dirname(_logp) or ".", exist_ok=True)
    log_f = open(_logp, "w", encoding="utf-8")
    random.seed(a.seed)
    np.random.seed(a.seed)
    torch.manual_seed(a.seed)

    log(log_f, "=== train_bc_v6 ===")
    log(log_f, "data-dir :", a.data_dir)
    log(log_f, "init     :", a.init_weights)
    log(log_f, "outcome  :", a.outcome or "(全收)")

    games = load_shards(a.data_dir, a.outcome, a.max_games, log_f)
    if not games:
        log(log_f, "!! 没有可用 shard（检查 --data-dir / --outcome）")
        return 2
    nsteps = sum(len(g["am"]) for g in games)
    log(log_f, f"局数 {len(games)} · 决策步 {nsteps}")

    # ---- 拼成三个大数组（obs 按局保持，避免一次性 concat 爆内存）----
    rng = np.random.default_rng(a.seed)
    order = rng.permutation(len(games))
    n_val = max(1, int(len(games) * a.val_split)) if len(games) > 4 else 0
    val_idx = set(order[:n_val].tolist())

    # ---- 模型 ----
    kwargs = read_arch_kwargs(a.init_weights)
    model = PPOStudent(**kwargs).to(a.device)
    load_state_into(model, a.init_weights)  # 第二个参数是**路径**（见 weights_io.py:147）
    n_par = sum(p.numel() for p in model.parameters())
    log(log_f, f"模型 params {n_par} · action_v6={model.action_v6} · device={a.device}")

    # ---- class weight（fire / hold 都偏）----
    def counts(key, k):
        c = np.zeros(k, np.int64)
        for g in games:
            np.add.at(c, g[key], 1)
        return c

    def inv_freq(c):
        w = (c.sum() / (len(c) * np.maximum(c, 1))).astype(np.float32)
        return torch.tensor(w, device=a.device)

    c_m, c_f, c_h = counts("am", 4), counts("af", 3), counts("ah", 2)
    log(log_f, f"类别分布 move {c_m.tolist()} / fire {c_f.tolist()} / hold {c_h.tolist()}")
    w_fire, w_hold = inv_freq(c_f), inv_freq(c_h)

    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, a.epochs))

    def run_epoch(ids, train: bool):
        tot: dict[str, float] = {"loss": 0.0, "move": 0.0, "fire": 0.0, "hold": 0.0, "n": 0.0}
        model.train(train)
        for gi in ids:
            g = games[gi]
            n = len(g["am"])
            perm = torch.randperm(n) if train else torch.arange(n)
            for s in range(0, n, a.batch):
                sel = perm[s:s + a.batch]
                if len(sel) < 2:
                    continue
                obs = torch.from_numpy(g["obs"][sel.numpy()]).to(a.device)
                scal = torch.from_numpy(g["scal"][sel.numpy()]).to(a.device)
                extra = torch.from_numpy(g["extra"][sel.numpy()]).to(a.device)
                am = torch.from_numpy(g["am"][sel.numpy()]).to(a.device)
                af = torch.from_numpy(g["af"][sel.numpy()]).to(a.device)
                ah = torch.from_numpy(g["ah"][sel.numpy()]).to(a.device)
                with torch.set_grad_enabled(train):
                    m_logit, f_logit, h_logit, _v = model(obs, scal, extra)
                    l_m = F.cross_entropy(m_logit, am)
                    l_f = F.cross_entropy(f_logit, af, weight=w_fire)
                    l_h = F.cross_entropy(h_logit, ah, weight=w_hold)
                    loss = l_m + l_f + l_h
                if train:
                    opt.zero_grad(set_to_none=True)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()
                tot["loss"] += float(loss) * len(sel)
                tot["move"] += int((m_logit.argmax(1) == am).sum())
                tot["fire"] += int((f_logit.argmax(1) == af).sum())
                tot["hold"] += int((h_logit.argmax(1) == ah).sum())
                tot["n"] += len(sel)
        den = max(1.0, tot["n"])
        return tot["loss"] / den, tot["move"] / den, tot["fire"] / den, tot["hold"] / den

    tr = [i for i in range(len(games)) if i not in val_idx]
    va = sorted(val_idx)
    log(log_f, f"train 局 {len(tr)} / val 局 {len(va)}")

    best = float("inf")
    for ep in range(1, a.epochs + 1):
        lo, mo, fo, ho = run_epoch(tr, True)
        sched.step()
        line = f"ep{ep:<3d} train loss {lo:.4f} move {mo:.3f} fire {fo:.3f} hold {ho:.3f}"
        if va:
            vl, vm, vf, vh = run_epoch(va, False)
            line += f" | val loss {vl:.4f} move {vm:.3f} fire {vf:.3f} hold {vh:.3f}"
            if vl < best:
                best = vl
                _save(model, a.out + ".best", a)
        log(log_f, line)

    _save(model, a.out, a)
    log(log_f, "done →", a.out)
    return 0


def _save(model, path: str, a) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    save_weights_json(model, path)
    print("[saved]", path)


if __name__ == "__main__":
    raise SystemExit(main())

"""PPO 吞吐探针 —— 自包含单文件，不依赖仓库任何模块。

两个用途：

  ① **TPU 可行性**：在 Kaggle TPU notebook 上量 PPO 单步耗时 (s/step)，回答
     「GPU 配额用完后切 TPU 能不能净增 20h/周算力」。判据线来自真实训练数据：
        本机 CPU   ~700 s/轮  -> 4.7 s/step
        Kaggle GPU ~70  s/轮  -> 0.47 s/step
     （148 steps = 37 chunks x 4 epochs，见 tmp/<course>/training_log.jsonl）

  ② **性能优化效果**：分离测出各优化项的收益（2026-09-10）
       A 纯 fwd+bwd+step（固定 shape）        基准
       B = A + 每步 8 次 .item() 同步         同步税
       C = B + 变化尾块                        XLA 重编译税（TPU 独有）
       D1 = B + ref 每步重算（旧 engine 行为） 双前向税
       D2 = B + ref 预计算缓存（新 engine 行为）ref 成本降到 1/epochs
     D1 vs D2 就是 ppo/engine.py 本次落地的 ref_cache 优化的实测收益
     （数值逐位不变，见 tests/test_ppo_kickstart_cache.py）。

用法：
    本机对照：     python tools/tpu-probe.py --device cpu --quick
    只测优化效果：  python tools/tpu-probe.py --device cpu --skip-shapes
    Kaggle GPU：  python tools/tpu-probe.py --device cuda --skip-shapes
    Kaggle TPU：  见 ipynb/tpu-probe.ipynb —— **必须在内核进程内跑**
                  （%%writefile 后 import tpu_probe; tpu_probe.main()）。
                  ⚠ 不要用 `!python tpu-probe.py`：TPU 是独占的 PCI 直通设备
                  (/dev/vfio/*)，子进程抢不到它，会报
                    RuntimeError: open(/dev/vfio/0): Device or resource busy
                  （2026-09-10 实测踩到）。同理，notebook 的环境探测 cell 也不能调
                  xla_device()，否则内核会先把设备占住，后续子进程必失败。

模型与 models/student.py 逐位一致（BN-free ConvMixer-Lite h=64 d=8，stem 权重已折进
1/255）；训练循环与 ppo/engine.py::ppo_update 的算子结构等价。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# ---------------------------------------------------------------- 常量（= schema.py）
BOARD = 26
OBS_CHANNELS = 14
SCALAR_DIM = 19
MOVE_DIM = 5
FIRE_DIM = 2
H = 64
D = 8
HEAD_HIDDEN = 128

MB = 512  # 与 rl-config / 课程一致
CHUNKS = 37  # c5-margin 实测每轮 36-37 块
EPOCHS = 4

CPU_LINE = 4.7  # 700 s / 148 steps
GPU_LINE = 0.47  #  70 s / 148 steps


# ---------------------------------------------------------------- 模型（内联副本）
class ConvMixerBlock(nn.Module):
    def __init__(self, h: int):
        super().__init__()
        self.dw = nn.Conv2d(h, h, 5, padding=2, groups=h, bias=True)
        self.pw = nn.Conv2d(h, h, 1, bias=True)

    def forward(self, x):
        return x + F.relu(self.pw(F.relu(self.dw(x))))


def coord_channels(board: int, device) -> torch.Tensor:
    r = torch.arange(board, dtype=torch.float32, device=device) / (board - 1)
    x = r.repeat(board, 1)
    y = x.t()
    return (torch.stack([x, y]) * 255).round().to(torch.uint8)


class PPOStudent(nn.Module):
    """= models/student.py::PPOStudent（BN-free，stem 含 1/255 折入）。"""

    def __init__(
        self,
        in_ch=OBS_CHANNELS,
        board=BOARD,
        scalar_dim=SCALAR_DIM,
        h=H,
        d=D,
        head_hidden=HEAD_HIDDEN,
    ):
        super().__init__()
        self.board, self.head_hidden = board, head_hidden
        self.stem = nn.Conv2d(in_ch + 2, h, 3, padding=1, bias=True)
        self.blocks = nn.ModuleList([ConvMixerBlock(h) for _ in range(d)])
        self.fc = nn.Linear(h + scalar_dim, head_hidden, bias=True)
        self.move_head = nn.Linear(head_hidden, MOVE_DIM, bias=True)
        self.fire_head = nn.Linear(head_hidden, FIRE_DIM, bias=True)
        self.value_head = nn.Linear(head_hidden, 1, bias=True)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                nn.init.zeros_(m.bias)
        with torch.no_grad():
            self.stem.weight.mul_(1.0 / 255.0)

    def features(self, obs, scalars):
        coords = coord_channels(self.board, obs.device).float().unsqueeze(0)
        x = torch.cat([obs.float(), coords.expand(obs.shape[0], -1, -1, -1)], dim=1)
        x = F.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        x = x.mean(dim=(2, 3))
        return F.relu(self.fc(torch.cat([x, scalars], dim=1)))

    def forward(self, obs, scalars):
        h = self.features(obs, scalars)
        return self.move_head(h), self.fire_head(h), self.value_head(h)


# ---------------------------------------------------------------- batch 合成
def make_chunk(b: int, rng: np.random.Generator) -> dict:
    return {
        "obs": rng.integers(0, 256, (b, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": rng.standard_normal((b, SCALAR_DIM)).astype(np.float32),
        "a_move": rng.integers(0, MOVE_DIM, (b,), dtype=np.int64),
        "a_fire": rng.integers(0, FIRE_DIM, (b,), dtype=np.int64),
        "lp_move": rng.standard_normal(b).astype(np.float32),
        "lp_fire": rng.standard_normal(b).astype(np.float32),
        "adv": rng.standard_normal(b).astype(np.float32),
        "ret": rng.standard_normal(b).astype(np.float32),
        "mask": np.ones((b, MOVE_DIM + FIRE_DIM), dtype=np.float32),
    }


# ---------------------------------------------------------------- 算子（= ppo/common.py）
def masked_logsoftmax(logits, mask):
    big = torch.tensor(1e9, device=logits.device, dtype=logits.dtype)
    m = mask.to(logits.dtype)
    return F.log_softmax(logits + (1.0 - m) * (-big), dim=-1)


def cat_logprob(a, lp):
    return lp.gather(1, a.unsqueeze(1)).squeeze(1)


def cat_entropy(lp):
    return -(lp.exp() * lp).sum(dim=-1).mean()


# ---------------------------------------------------------------- 设备适配
class Backend:
    """统一 CPU / CUDA / TPU 的 step 与统计同步语义。"""

    def __init__(self, kind: str):
        self.kind = kind
        self.xm: Any = None
        self.dp = False
        if kind == "tpu":
            os.environ.setdefault("PJRT_DEVICE", "TPU")
            import torch_xla
            import torch_xla.core.xla_model as xm

            self.xm = xm
            self._torch_xla = torch_xla
            # 2.5+ 推荐 torch_xla.device()；旧版只有 xm.xla_device()（会给 DeprecationWarning）
            dev_fn = getattr(torch_xla, "device", None)
            try:
                self.device = dev_fn() if callable(dev_fn) else xm.xla_device()
            except RuntimeError as e:
                # 最常见：/dev/vfio/* 被占用 —— TPU 是**独占**的 PCI 直通设备。任何人
                # （包括 notebook 内核里一次 import torch_xla 触发的 PJRT 自动探测）
                # 先碰过它，后来者都会拿到 Device or resource busy。
                print(
                    f"\n[TPU 初始化失败] {e}\n"
                    "  可能原因与处置：\n"
                    "  1) /dev/vfio/* 已被占用（TPU 独占）—— 这是绝大多数情况。\n"
                    "     Colab/Kaggle: Runtime -> Restart session（或 Disconnect and delete\n"
                    "     runtime）释放设备后，从第一个 cell 重跑本 notebook。\n"
                    "  2) 本 notebook 的探测 cell 不该 import torch_xla —— torch_xla 2.6+\n"
                    "     导入时会自动探测 PJRT device，可能直接占住 TPU。用\n"
                    "     importlib.util.find_spec 查安装状态即可。\n"
                    "  3) 确认 Accelerator 选的是 TPU（Colab: TPU v5e-1 / Kaggle: TPU VM v3-8）。\n",
                    file=sys.stderr,
                )
                raise
        elif kind in ("cuda", "cuda-dp"):
            self.device = torch.device("cuda")
            self.dp = kind == "cuda-dp"
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        else:
            self.device = torch.device("cpu")

    def wrap(self, model):
        """cuda-dp 且可见 >1 卡 -> DataParallel；其余情况原样返回（零改动）。"""
        if getattr(self, "dp", False) and torch.cuda.device_count() > 1:
            return torch.nn.DataParallel(model)
        return model

    def to(self, m):
        return m.to(self.device)

    def step(self, opt):
        if self.kind == "tpu":
            # 官方口径：梯度跨副本归约 + 显式图执行边界。裸 opt.step() 在
            # XRT/GSPMD 路径上会漏掉执行边界（静默不更新）。
            self.xm.optimizer_step(opt)
        else:
            opt.step()

    def sync(self):
        """**真正的**设备同步（等 GPU 算完）。mark() 在 CUDA 上是 no-op，
        若不额外 sync，sync_mode="none" 测到的只是 CPU 入队时间 —— 无效测量
        （2026-09-10 实测：A=64ms 而 B_new=192ms，差的不是同步税，是"A 根本没等"）。"""
        if self.kind in ("cuda", "cuda-dp"):
            torch.cuda.synchronize()
        elif self.kind == "tpu":
            self.mark()

    def mark(self):
        if self.kind == "tpu":
            # torch_xla 2.5+ 起 mark_step 改名为 sync()（实测 Kaggle/Colab 都会给
            # xm.mark_step() 发 "Use torch_xla.sync instead"）。按新名优先探测。
            for _name in ("sync", "mark_step"):
                _fn = getattr(self._torch_xla, _name, None)
                if callable(_fn):
                    _fn()
                    return
            self.xm.mark_step()


# ---------------------------------------------------------------- 训练步
def prepare(bk: Backend, chunks: list[dict]) -> list[dict]:
    """numpy chunks -> 设备张量，只做一次（= engine 的 tensored_chunks）。"""
    out = []
    for c in chunks:
        out.append({k: torch.from_numpy(v).to(bk.device) for k, v in c.items()})
    bk.sync()
    return out


def build_ref_cache(bk: Backend, ref_model, dev_chunks: list[dict]) -> list:
    """新 engine 行为：每 chunk 只算一次 ref，按索引复用（跨 epoch 不变）。"""
    out = []
    with torch.no_grad():
        for e in dev_chunks:
            rm, rf, _ = ref_model(e["obs"], e["scalars"])
            m = e["mask"]
            out.append(
                (
                    masked_logsoftmax(rm, m[:, :MOVE_DIM]),
                    masked_logsoftmax(rf, m[:, MOVE_DIM : MOVE_DIM + FIRE_DIM]),
                )
            )
    bk.mark()
    return out


def run_steps(
    bk: Backend,
    model,
    opt,
    dev_chunks,
    epochs,
    *,
    sync_mode: str = "batched",
    ref_model=None,
    ref_cache=None,
) -> tuple[float, int]:
    """跑 epochs x len(chunks) 个梯度步，返回 (总秒数, 步数)。

    sync_mode: none（不同步，算力下限）/ per-item（旧引擎 8 次 .item()）/
    batched（新引擎 1 次 stack().tolist()）。per-item - batched 即该优化的净值。


    ref_model 非空 -> 旧行为（每步现算 ref）；ref_cache 非空 -> 新行为（预计算复用）。
    """
    n_steps = len(dev_chunks) * epochs
    t0 = time.time()
    for _ep in range(epochs):
        for ci, e in enumerate(dev_chunks):
            obs, sc, mask = e["obs"], e["scalars"], e["mask"]
            mv, fr, val = model(obs, sc)
            move_logp = masked_logsoftmax(mv, mask[:, :MOVE_DIM])
            fire_logp = masked_logsoftmax(fr, mask[:, MOVE_DIM : MOVE_DIM + FIRE_DIM])
            lp_new = cat_logprob(e["a_move"], move_logp) + cat_logprob(e["a_fire"], fire_logp)
            lp_old = e["lp_move"] + e["lp_fire"]
            ratio = torch.exp(lp_new - lp_old)
            adv = e["adv"]
            surr1 = ratio * adv
            surr2 = torch.clamp(ratio, 0.8, 1.2) * adv
            policy_loss = -torch.min(surr1, surr2).mean()
            value_loss = F.mse_loss(val.squeeze(-1), e["ret"])
            entropy = cat_entropy(move_logp) + cat_entropy(fire_logp)
            loss = policy_loss + 1.0 * value_loss - 0.01 * entropy

            kick_mean = torch.zeros((), device=bk.device)
            ref_pair = ref_cache[ci] if ref_cache is not None else None
            if ref_pair is None and ref_model is not None:
                with torch.no_grad():
                    rm, rf, _ = ref_model(obs, sc)
                    ref_pair = (
                        masked_logsoftmax(rm, mask[:, :MOVE_DIM]),
                        masked_logsoftmax(rf, mask[:, MOVE_DIM : MOVE_DIM + FIRE_DIM]),
                    )
            if ref_pair is not None:
                ref_move, ref_fire = ref_pair
                kl_m = (move_logp.exp() * (move_logp - ref_move)).sum(dim=-1)
                kl_f = (fire_logp.exp() * (fire_logp - ref_fire)).sum(dim=-1)
                kick_mean = (kl_m + kl_f).mean()
                loss = loss + 1.0 * kick_mean

            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            bk.step(opt)

            if sync_mode == "per-item":
                # 旧 ppo/engine.py 的真实形态：每步 8 次主机同步
                _ = float(policy_loss.item())
                _ = float(value_loss.item())
                _ = float(entropy.item())
                _ = float(ratio.mean().item())
                _ = float(adv.mean().item())
                _ = float(e["ret"].mean().item())
                _ = float(lp_new.mean().item())
                _ = float(val.mean().item())
            elif sync_mode == "batched":
                # 新形态（ppo/common.sync_scalars）：8 个标量一次 stack().tolist()
                _ = torch.stack(
                    [
                        policy_loss.detach().reshape(()),
                        value_loss.detach().reshape(()),
                        entropy.detach().reshape(()),
                        ratio.mean().detach().reshape(()),
                        adv.mean().detach().reshape(()),
                        e["ret"].mean().detach().reshape(()),
                        lp_new.mean().detach().reshape(()),
                        val.mean().detach().reshape(()),
                    ]
                ).tolist()
            else:
                bk.mark()
    bk.sync()          # 真正的设备同步：CUDA 上必须等，否则计时不含 GPU 时间
    return time.time() - t0, n_steps


def bench_case(
    bk,
    chunks,
    epochs,
    *,
    sync_mode="batched",
    ref_cache=None,
    ref_model=None,
    warmup=1,
    repeat=2,
    seed=0,
):
    """warmup 掉 XLA 编译，再取 repeat 次最小值。"""
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = bk.wrap(bk.to(PPOStudent()))
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    dev_chunks = prepare(bk, chunks)
    for _ in range(warmup):
        run_steps(
            bk,
            model,
            opt,
            dev_chunks[: min(2, len(dev_chunks))],
            1,
            sync_mode=sync_mode,
            ref_model=ref_model,
            ref_cache=ref_cache,
        )
    best = float("inf")
    n = 0
    for _ in range(repeat):
        s, n = run_steps(
            bk,
            model,
            opt,
            dev_chunks,
            epochs,
            sync_mode=sync_mode,
            ref_model=ref_model,
            ref_cache=ref_cache,
        )
        best = min(best, s)
    return best / n, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="tpu", choices=["tpu", "cuda", "cuda-dp", "cpu"])
    ap.add_argument("--chunks", type=int, default=CHUNKS)
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--quick", action="store_true", help="本地自检：小规模（4 块 x 1 epoch）")
    ap.add_argument(
        "--skip-shapes", action="store_true", help="跳过 C 段（变化 shape，TPU 重编译税）"
    )
    ap.add_argument("--skip-ref", action="store_true", help="跳过 D1/D2 段（ref 双前向对照）")
    ap.add_argument(
        "--skip-sync-tax",
        action="store_true",
        help="跳过 B_old（逐项 .item() 同步税）。TPU 上这一段每步要 8 次整图 materialize，"
        "慢到分钟级；只想要 A/B_new/D 段时用它。",
    )
    ap.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="每个 case 的编译预热轮数（默认 1；TPU 想更快可给 0）",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="每个 case 的计时重复次数，取最小值（默认 2；想更快可给 1）",
    )
    args = ap.parse_args()

    if args.quick:
        args.chunks, args.epochs = 4, 1

    bk = Backend(args.device)
    n_par = sum(p.numel() for p in PPOStudent().parameters())
    n_steps_iter = 148  # 37 x 4，用于把 s/step 折成 s/轮
    def announce(tag: str, desc: str) -> None:
        """case 级进度：TPU 上某些段慢到分钟级，不打印会误判成卡死。"""
        print(f"  … 开始 {tag}: {desc}", flush=True)

    print("=" * 72)
    print(f"PPOStudent params={n_par:,}  device={args.device} -> {bk.device}")
    print(f"chunks={args.chunks} x epochs={args.epochs} = {args.chunks * args.epochs} steps")
    if args.device.startswith("cuda") and torch.cuda.is_available():
        _n = torch.cuda.device_count()
        _names = ", ".join(torch.cuda.get_device_name(i) for i in range(_n))
        print(f"可见 GPU: {_n} 张 [{_names}]" + ("  -> DataParallel 生效" if _n > 1 and args.device == "cuda-dp" else ""))
        if _n > 1 and args.device == "cuda":
            print("  ⚠ 你有多张卡但用了 --device cuda（只用第 0 张）。想跨卡跑用 --device cuda-dp。")
    print("=" * 72)

    rng = np.random.default_rng(20260910)
    fixed = [make_chunk(MB, rng) for _ in range(args.chunks)]
    # 变化尾块：模拟真实每轮不同的 n mod 512（c5-margin 74 轮出现 66 种）
    var_tails = [230, 39, 456, 365, 424, 65]

    cases: list[tuple[str, float]] = []

    announce("A", "纯 fwd+bwd+step（固定 shape, 无同步）")
    s, n = bench_case(
        bk, fixed, args.epochs, sync_mode="none", warmup=args.warmup, repeat=args.repeat
    )
    cases.append(("A 纯 fwd+bwd+step（固定 shape, 无同步）", s))
    print(f"  A: {s * 1000:7.0f} ms/step", flush=True)

    if not args.skip_sync_tax:
        announce("B_old", "8x .item()/step —— TPU 上每步 8 次整图 materialize，可能分钟级")
        s, n = bench_case(
            bk, fixed, args.epochs, sync_mode="per-item", warmup=args.warmup, repeat=args.repeat
        )
        cases.append(("B_old = A + 8x .item()/step（旧引擎）", s))
        print(f"  B_old: {s * 1000:7.0f} ms/step", flush=True)

    announce("B_new", "1x stack().tolist()（新引擎）")
    s, n = bench_case(
        bk, fixed, args.epochs, sync_mode="batched", warmup=args.warmup, repeat=args.repeat
    )
    cases.append(("B_new = A + 1x stack().tolist()（新引擎）", s))
    print(f"  B_new: {s * 1000:7.0f} ms/step", flush=True)

    if not args.skip_shapes:
        worst = 0.0
        for tail in var_tails:
            ch = [make_chunk(MB, rng) for _ in range(max(1, args.chunks - 1))] + [
                make_chunk(tail, rng)
            ]
            announce("C", f"变化尾块 tail={tail}（新 shape -> XLA 重编译，慢）")
            s, n = bench_case(
                bk, ch, args.epochs, sync_mode="batched", warmup=args.warmup, repeat=1
            )
            worst = max(worst, s)
            print(f"  C tail={tail:4d}: {s * 1000:7.0f} ms/step", flush=True)
        cases.append(("C = B + 变化尾块（XLA 重编译最坏值）", worst))

    d1 = d2 = None
    if not args.skip_ref:
        torch.manual_seed(0)
        ref = bk.wrap(bk.to(PPOStudent())).eval()
        for p in ref.parameters():
            p.requires_grad_(False)
        dev_fixed = prepare(bk, fixed)
        cache = build_ref_cache(bk, ref, dev_fixed)

        announce("D1", "ref 每步重算（旧 engine）")
        d1, n = bench_case(
            bk,
            fixed,
            args.epochs,
            sync_mode="batched",
            ref_model=ref,
            warmup=args.warmup,
            repeat=args.repeat,
        )
        cases.append(("D1 = B + ref 每步重算（旧 engine）", d1))
        print(f"  D1: {d1 * 1000:7.0f} ms/step  (ref 每步现算)", flush=True)

        announce("D2", "ref 预计算缓存（新 engine）")
        d2, n = bench_case(
            bk,
            fixed,
            args.epochs,
            sync_mode="batched",
            ref_cache=cache,
            warmup=args.warmup,
            repeat=args.repeat,
        )
        cases.append(("D2 = B + ref 预计算缓存（新 engine）", d2))
        print(f"  D2: {d2 * 1000:7.0f} ms/step  (ref 每 chunk 一次)", flush=True)

    print("=" * 72)
    print(f"{'case':<44}{'s/step':>10}{'s/轮':>10}")
    print("-" * 72)
    for name, s in cases:
        print(f"{name:<44}{s:>10.3f}{s * n_steps_iter:>10.0f}")
    print("-" * 72)
    print(f"{'本机 CPU 实测线（用户口径）':<44}{CPU_LINE:>10.2f}{700:>10.0f}")
    print(f"{'Kaggle GPU 实测线（用户口径）':<44}{GPU_LINE:>10.2f}{70:>10.0f}")
    print("=" * 72)

    got = dict(cases)
    b_old = got.get("B_old = A + 8x .item()/step（旧引擎）")
    b = got["B_new = A + 1x stack().tolist()（新引擎）"]
    print()
    ga = got["A 纯 fwd+bwd+step（固定 shape, 无同步）"]
    print(f"[优化效果]  同步税 新 B_new-A = {(b - ga) * 1000:+.0f} ms/step")
    if b_old is not None:
        print(f"[优化效果]  同步税 旧 B_old-A = {(b_old - ga) * 1000:+.0f} ms/step")
        print(
            f"[优化效果]  .item()->批量化   = {(b_old - b) * 1000:+.0f} ms/step "
            f"({(b_old - b) / b_old * 100:.1f}% of B_old)"
        )
    else:
        print("[优化效果]  同步税 旧 B_old-A = （本次 --skip-sync-tax 跳过）")
    if d1 and d2:
        print(
            f"[优化效果]  ref 缓存 D1-D2 = {(d1 - d2) * 1000:+.0f} ms/step "
            f"({(d1 - d2) / d1 * 100:.1f}% of D1)"
        )
        print(
            f"[优化效果]  真实形态端到端 = {d1:.3f} -> {d2:.3f} s/step "
            f"({d1 / d2:.2f}x)，折算 {(d1 - d2) * n_steps_iter:.0f}s/轮"
        )
    print()
    if b < GPU_LINE:
        print(
            f"[TPU 判据] B={b:.3f}s/step < GPU 线 {GPU_LINE} —— TPU 可**顶替** GPU，值得全量接入。"
        )
    elif b < CPU_LINE:
        print(
            f"[TPU 判据] B={b:.3f}s/step < CPU 线 {CPU_LINE} —— TPU 可**叠加**在 GPU 配额之后"
            f"（20h/周净增）。值得接入。"
        )
    else:
        print(f"[TPU 判据] B={b:.3f}s/step >= CPU 线 {CPU_LINE} —— TPU 不比本机 CPU 快。")
        print("           若 C 段远高于 B，先修尾块 shape（pad 到 mb，消掉每轮重编译）再测。")


if __name__ == "__main__":
    main()

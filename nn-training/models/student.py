"""
Student policy network — CoordConv-ConvMixer-Lite (plan/RL-Net-Selection.md §4.3).

Distilled from the RL teacher (rl_model.py, ResNet 950K + BN) — or, for P1.5,
from the live God-AI teacher (GodAIInput labels). BN-free by construction so the
pure-TS runtime (`src/nn/infer.ts`) can reproduce the forward pass
byte-for-byte from the exported weights (plan §NN-M1 determinism ②).

Architecture (h=64 / d=8 sweet spot, plan §4.3):
  obs(14×26×26) + 2 coord channels (x/y normalized, computed in forward)
    → stem  Conv 16→h, 3×3, ReLU
    → d ×   ConvMixer block:
              depthwise  h→h, 5×5, groups=h, ReLU
              pointwise  h→h, 1×1, ReLU
              残差连接
    → GAP  → (h,)  + concat scalars(19) → (h+19,)
    → FC   (h+19)→128, ReLU
    → 双头(v2): move-5 / fire-2   （item 头删除 —— AI 不使用主动道具）

Params (h=64, d=8, v2 实算): 67.5K — v1 三头旧口径 "~69K" 已废弃（P2k3-8）.
MAdds (26×26): ~37M.
Coord channel formula — MUST match the TS runtime exactly:
  ch0[row][col] = round(col/(BOARD-1) * 255)   // x, varies along columns
  ch1[row][col] = round(row/(BOARD-1) * 255)   // y, varies along rows
(uint8 0..255, same scale as the encoder's uint8 obs; obs.float() keeps 0..255.)
"""



from __future__ import annotations

# 仓库根探测（B4，2026-09-02）：包已安装（pip install -e .）或 script-dir/cwd 在
# nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入 sys.path——
# 不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正 import，
# 避免探针导入产生 F401。
import importlib.util as _ilu

if _ilu.find_spec("schema") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import os

import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import BOARD, FIRE_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM

DEFAULT_H = 64
DEFAULT_D = 8
DEFAULT_HEAD_HIDDEN = 128


class ConvMixerBlock(nn.Module):
    """depthwise 5×5 + pointwise 1×1, ReLU between, residual across."""

    def __init__(self, h: int):
        super().__init__()
        self.dw = nn.Conv2d(h, h, 5, padding=2, groups=h, bias=True)
        self.pw = nn.Conv2d(h, h, 1, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = F.relu(self.pw(F.relu(self.dw(x))))
        return x + y


def coord_channels(board: int, device) -> torch.Tensor:
    """(2, board, board) uint8 — x/y normalized channels (see module doc)."""
    r = torch.arange(board, dtype=torch.float32, device=device) / (board - 1)
    x = r.repeat(board, 1)  # row i: [j] = j/(board-1)
    y = x.t()  # row i: [j] = i/(board-1)
    ch = torch.stack([x, y])
    return (ch * 255).round().to(torch.uint8)


class StudentNet(nn.Module):
    """
    CoordConv-ConvMixer-Lite student (plan §4.3), BN-free.

    Input:
      obs:     (B, 14, 26, 26) uint8 — encoder output (14 channels)
      scalars: (B, 19) float32
    Output: (move_logits, fire_logits)（v2：双头，item 头已删除）.
    """

    def __init__(
        self,
        in_ch: int = OBS_CHANNELS,
        board: int = BOARD,
        scalar_dim: int = SCALAR_DIM,
        h: int = DEFAULT_H,
        d: int = DEFAULT_D,
        head_hidden: int = DEFAULT_HEAD_HIDDEN,
    ):
        super().__init__()
        self.in_ch = in_ch
        self.board = board
        self.scalar_dim = scalar_dim
        self.h = h
        self.d = d
        self.head_hidden = head_hidden

        self.stem = nn.Conv2d(in_ch + 2, h, 3, padding=1, bias=True)  # +2 coord channels
        self.blocks = nn.ModuleList([ConvMixerBlock(h) for _ in range(d)])
        self.fc = nn.Linear(h + scalar_dim, head_hidden, bias=True)
        self.move_head = nn.Linear(head_hidden, MOVE_DIM, bias=True)
        self.fire_head = nn.Linear(head_hidden, FIRE_DIM, bias=True)

        self._init_weights()
        # P1-10（2026-09-02）：输入归一化**折进首层权重**。obs 与 coord 通道同为
        # 0..255，对 stem.weight 统一 ×1/255 在数学上等价于 forward 里 input/255
        # ——但权重文件格式不变，TS 运行时 src/nn/infer.ts **零改动**（它不做除法，
        # 权重已含缩放）。历史教训：不归一化导致 trunk 激活 ~千级、策略头 logits
        # ±7600、熵≈0.01，PPO 无法探索（run_rl.py warm_start_normalize 手工兜底
        # 的根源，plan/python-refactor.md P1-10）。
        with torch.no_grad():
            self.stem.weight.mul_(1.0 / 255.0)

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                nn.init.zeros_(m.bias)

    def arch(self) -> dict:
        return {
            "kind": "student",
            "in_ch": self.in_ch,
            "board": self.board,
            "scalar_dim": self.scalar_dim,
            "h": self.h,
            "d": self.d,
            "head_hidden": self.head_hidden,
        }

    def features(self, obs: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        """Shared trunk → hidden (B, head_hidden). Reused by PPO value head."""
        coords = coord_channels(self.board, obs.device).float().unsqueeze(0)
        x = torch.cat(
            [obs.float(), coords.expand(obs.shape[0], -1, -1, -1)], dim=1
        )  # (B, 16, 26, 26)
        x = F.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        x = x.mean(dim=(2, 3))  # GAP → (B, h)
        x = torch.cat([x, scalars], dim=1)  # (B, h + 19)
        return F.relu(self.fc(x))

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor
    ) -> tuple[torch.Tensor, ...]:
        """obs: (B,14,26,26) u1; scalars: (B,19) f4 → (move_logits, fire_logits).

        返回类型刻意写成变长 tuple：子类（PPOStudent / IntentNet / GoalNet）会在
        尾部追加 value 等头，固定 2 元组会让每处 override 都违反 LSP。运行时不变。
        """
        h = self.features(obs, scalars)
        return self.move_head(h), self.fire_head(h)


class PPOStudent(StudentNet):
    """
    RL-ready variant: StudentNet trunk + 2 factored policy heads + a value head.
    Value head is trained by PPO (init random; BC checkpoints lack it).
    Exports the SAME weight keys as StudentNet plus `value_head.{weight,bias}`,
    so the TS runtime (`src/nn/infer.ts` StudentModel) can load it via the
    value_head optional slot and serve V(s) for on-policy rollout.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.value_head = nn.Linear(self.head_hidden, 1)

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        h = self.features(obs, scalars)
        return self.move_head(h), self.fire_head(h), self.value_head(h)

    @torch.no_grad()
    def predict(self, obs: torch.Tensor, scalars: torch.Tensor | None = None):
        """Inference helper: returns softmax-prob dicts (mirrors TS infer)."""
        self.eval()
        if scalars is None:
            scalars = torch.zeros(obs.shape[0], self.scalar_dim)
        m, f, _v = self.forward(obs, scalars)
        return (
            torch.softmax(m, dim=-1),
            torch.softmax(f, dim=-1),
        )


def param_count(model: nn.Module) -> int:
    return sum(int(p.numel()) for p in model.parameters())


def export_student_golden(path: str, h: int = DEFAULT_H, d: int = DEFAULT_D, seed: int = 20260903) -> None:
    """轴 2 parity golden（PPOStudent 生产路径，kind='student'）。

    输出 JSON：{ format:"student-golden", version, h, d, head_hidden, seed,
                 obs, scalars, moveLogits:[5], fireLogits:[2], valueLogits:[1],
                 params（stem/blocks/fc/move_head/fire_head/value_head）}
    TS 端 buildModelFromText(arch.kind='student', h, d) → forward() 对比三头。

    为什么需要（2026-09-03 审计）：goal/intent golden 只覆盖 StudentNet 主干+专用头，
    从不触碰 **per-tick 策略头（move_head/fire_head + 128 宽 value_head）**——而这是
    export-rl-rollout.ts / s5-open20 活路径。任一侧改这些头或主干都会在此变红。"""
    torch.manual_seed(seed)
    rng = torch.Generator().manual_seed(seed)
    obs = torch.randint(0, 256, (1, OBS_CHANNELS, BOARD, BOARD), generator=rng, dtype=torch.uint8)
    sc = (torch.rand(1, SCALAR_DIM, generator=rng) - 0.5) * 4

    torch.manual_seed(seed + 1)
    m = PPOStudent(h=h, d=d).eval()
    with torch.no_grad():
        mv, fr, v = m(obs, sc)  # PPOStudent: (move, fire, value)

    from data.weights_io import tensor_to_b64

    params = {}
    for name, p in m.state_dict().items():
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
    golden = {
        "format": "student-golden",
        "version": 1,
        "h": h,
        "d": d,
        "head_hidden": m.head_hidden,
        "seed": seed,
        "obs": [int(v) for v in obs.flatten().tolist()],
        "scalars": [float(v) for v in sc.flatten().tolist()],
        "moveLogits": [float(v) for v in mv.flatten().tolist()],
        "fireLogits": [float(v) for v in fr.flatten().tolist()],
        "valueLogits": [float(v) for v in v.flatten().tolist()],
        "params": params,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(golden, f)
    print(f"student golden written: {path} (h={h} d={d} head_hidden={m.head_hidden} params={len(params)})")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--golden", metavar="OUT", help="PPOStudent golden 导出：固定权重+输入 → move/fire/value logits JSON"
    )
    ap.add_argument("--h", type=int, default=DEFAULT_H)
    ap.add_argument("--d", type=int, default=DEFAULT_D)
    ap.add_argument("--golden-seed", type=int, default=20260903)
    args = ap.parse_args()

    if args.golden:
        export_student_golden(args.golden, args.h, args.d, args.golden_seed)
        raise SystemExit(0)

    m = StudentNet()
    n = param_count(m)
    print(f"StudentNet params: {n} (~{n / 1000:.1f}K)  budget<=200K: {n <= 200_000}")
    dummy_obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    dummy_sc = torch.zeros(2, SCALAR_DIM)
    mv, fr = m(dummy_obs, dummy_sc)
    print("move", tuple(mv.shape), "fire", tuple(fr.shape))
    print("arch:", m.arch())

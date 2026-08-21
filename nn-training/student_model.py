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
    → GAP  → (h,)  + concat scalars(24) → (h+24,)
    → FC   (h+24)→128, ReLU
    → 三头: move-5 / fire-2 / item-3

Params (h=64, d=8): ~69K. MAdds (26×26): ~37M.
Coord channel formula — MUST match the TS runtime exactly:
  ch0[row][col] = round(col/(BOARD-1) * 255)   // x, varies along columns
  ch1[row][col] = round(row/(BOARD-1) * 255)   // y, varies along rows
(uint8 0..255, same scale as the encoder's uint8 obs; obs.float() keeps 0..255.)
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM, MOVE_DIM, FIRE_DIM, ITEM_DIM

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
    y = x.t()               # row i: [j] = i/(board-1)
    ch = torch.stack([x, y])
    return (ch * 255).round().to(torch.uint8)


class StudentNet(nn.Module):
    """
    CoordConv-ConvMixer-Lite student (plan §4.3), BN-free.

    Input:
      obs:     (B, 14, 26, 26) uint8 — encoder output (14 channels)
      scalars: (B, 24) float32
    Output: (move_logits, fire_logits, item_logits), each (B, K).
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
        self.item_head = nn.Linear(head_hidden, ITEM_DIM, bias=True)

        self._init_weights()

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
        x = torch.cat([obs.float(), coords.expand(obs.shape[0], -1, -1, -1)], dim=1)  # (B, 16, 26, 26)
        x = F.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        x = x.mean(dim=(2, 3))  # GAP → (B, h)
        x = torch.cat([x, scalars], dim=1)  # (B, h + 24)
        return F.relu(self.fc(x))

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        h = self.features(obs, scalars)
        return self.move_head(h), self.fire_head(h), self.item_head(h)


class PPOStudent(StudentNet):
    """
    RL-ready variant: StudentNet trunk + 3 factored policy heads + a value head.
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
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        h = self.features(obs, scalars)
        return self.move_head(h), self.fire_head(h), self.item_head(h), self.value_head(h)

    @torch.no_grad()
    def predict(self, obs: torch.Tensor, scalars: torch.Tensor | None = None):
        """Inference helper: returns softmax-prob dicts (mirrors TS infer)."""
        self.eval()
        if scalars is None:
            scalars = torch.zeros(obs.shape[0], self.scalar_dim)
        m, f, i = self.forward(obs, scalars)
        return (
            torch.softmax(m, dim=-1),
            torch.softmax(f, dim=-1),
            torch.softmax(i, dim=-1),
        )


def param_count(model: nn.Module) -> int:
    return sum(int(p.numel()) for p in model.parameters())


if __name__ == "__main__":
    m = StudentNet()
    n = param_count(m)
    print(f"StudentNet params: {n} (~{n/1000:.1f}K)  budget<=200K: {n <= 200_000}")
    dummy_obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    dummy_sc = torch.zeros(2, SCALAR_DIM)
    mv, fr, it = m(dummy_obs, dummy_sc)
    print("move", tuple(mv.shape), "fire", tuple(fr.shape), "item", tuple(it.shape))
    print("arch:", m.arch())

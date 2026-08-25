"""
NN policy network — fully-convolutional backbone + scalar fusion + 2 factored heads.

Architecture (v2 — scalars fused, item head removed):
  obs  → Conv(14→32→48→64) → GAP → (B, 64)
  scalars (19-dim) → (B, 19)
  concat → (B, 83) → FC(83→64) → ReLU → heads

Design constraints (plan §NN-M1):
  * Only ReLU activations + self-implemented softmax at inference time, so
    the TS runtime (`src/nn/infer.ts`) can reproduce the forward pass
    byte-for-byte from the exported weights (plan §NN-M1 determinism ②).
  * Parameter budget <= ~200K. With conv_ch=(32,48,64) + scalar fusion
    this lands ~112K — deliberately small to match the 40-120K BC sample count.

Heads (v2):
  move  : 5  (none/up/down/left/right)   — predicted desired direction (hold)
  fire  : 2  (hold-state 0/1)            — label = firing bit at decision tick
"""
from __future__ import annotations

import torch
import torch.nn as nn

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM, MOVE_DIM, FIRE_DIM

DEFAULT_CONV_CH = (32, 48, 64)
DEFAULT_HEAD_HIDDEN = 64


class NNPolicy(nn.Module):
    def __init__(
        self,
        in_ch: int = OBS_CHANNELS,
        board: int = BOARD,
        scalar_dim: int = SCALAR_DIM,
        conv_ch: tuple[int, ...] = DEFAULT_CONV_CH,
        head_hidden: int = DEFAULT_HEAD_HIDDEN,
    ):
        super().__init__()
        self.in_ch = in_ch
        self.board = board
        self.scalar_dim = scalar_dim
        self.conv_ch = tuple(conv_ch)
        self.head_hidden = head_hidden

        # ---- Conv backbone (no batchnorm, no dropout — inference-reproducible) ----
        layers: list[nn.Module] = []
        c = in_ch
        for i, oc in enumerate(conv_ch):
            layers.append(nn.Conv2d(c, oc, kernel_size=3, padding=1, bias=True))
            layers.append(nn.ReLU(inplace=True))
            c = oc
        self.conv = nn.Sequential(*layers)
        # Global average pool collapses the spatial dims deterministically.
        self.gap = nn.AdaptiveAvgPool2d(1)
        # FC input = GAP output (c) + scalar features (scalar_dim)
        self.fc = nn.Linear(c + scalar_dim, head_hidden, bias=True)
        self.fc_relu = nn.ReLU(inplace=True)

        self.move_head = nn.Linear(head_hidden, MOVE_DIM, bias=True)
        self.fire_head = nn.Linear(head_hidden, FIRE_DIM, bias=True)

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
            "in_ch": self.in_ch,
            "board": self.board,
            "scalar_dim": self.scalar_dim,
            "conv_ch": list(self.conv_ch),
            "head_hidden": self.head_hidden,
        }

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        obs     : (B, 14, 26, 26) uint8
        scalars : (B, 19) float32 — 19-dim feature vector fused into the FC layer
        Returns (move_logits, fire_logits).
        """
        x = obs.float()
        x = self.conv(x)                 # (B, C, 26, 26)
        x = self.gap(x)                  # (B, C, 1, 1)
        x = x.flatten(1)                # (B, C)
        x = torch.cat([x, scalars], dim=1)  # (B, C + scalar_dim)
        h = self.fc_relu(self.fc(x))    # (B, head_hidden)
        return self.move_head(h), self.fire_head(h)

    @torch.no_grad()
    def predict(self, obs: torch.Tensor, scalars: torch.Tensor | None = None):
        """Inference helper: returns softmax-prob dicts (mirrors TS infer)."""
        self.eval()
        if scalars is None:
            scalars = torch.zeros(obs.shape[0], self.scalar_dim)
        m, f = self.forward(obs, scalars)
        return (
            torch.softmax(m, dim=-1),
            torch.softmax(f, dim=-1),
        )


def param_count(model: nn.Module) -> int:
    return sum(int(p.numel()) for p in model.parameters())


if __name__ == "__main__":
    m = NNPolicy()
    n = param_count(m)
    print(f"NNPolicy params: {n} (~{n/1000:.1f}K)  budget<=200K: {n <= 200_000}")
    dummy_obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    dummy_sc = torch.zeros(2, SCALAR_DIM)
    mv, fr = m(dummy_obs, dummy_sc)
    print("move", tuple(mv.shape), "fire", tuple(fr.shape))

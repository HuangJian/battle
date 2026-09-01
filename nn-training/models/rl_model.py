"""
RL Actor-Critic network — ResNet backbone + scalar fusion + dual heads.

STATUS (2026-08-21): NOT used by the live RL pipeline (run_rl.py → ppo.py →
PPOStudent). Kept as the teacher-model reference for the P1 phase
(teacher RL → distill to BN-free student, see plan/RL-Net-Selection.md §4).
Do not wire into the student pipeline without updating that plan.

Architecture:
  obs(14×26×26) → ConvIn(14→64) → 11×ResBlock(64) → ConvOut(64→128) → GAP
  scalars(19) → FC(19→64)
  concat(128+64=192) → Actor(192→256→7) + Critic(192→256→1)   (v2: item 删除)

Receptive field: 27×27 (104% coverage of 26×26 board)
Total params: ~999K
"""



from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import torch
import torch.nn as nn
import torch.nn.functional as F
from schema import BOARD, OBS_CHANNELS, SCALAR_DIM
from torch.distributions import Categorical

# Action space dimensions (v2: item 删除 —— AI 不使用主动道具)
MOVE_DIM = 5  # none/up/down/left/right
FIRE_DIM = 2  # hold/fire
TOTAL_ACTION_DIM = MOVE_DIM + FIRE_DIM  # 7


class ResBlock(nn.Module):
    """Pre-activation ResNet block with BatchNorm."""

    def __init__(self, channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(x + out)


class RLNet(nn.Module):
    """
    ResNet-based actor-critic for Battle City RL.

    Input:
      obs:     (B, 14, 26, 26) uint8 — 14-channel spatial observation
      scalars: (B, 19) float32 — 19-dim scalar features

    Output:
      action_logits: (B, 7) — [move(5), fire(2)]（v2：item 删除）
      value:         (B, 1)  — state value V(s)
    """

    def __init__(
        self,
        in_ch: int = OBS_CHANNELS,
        board: int = BOARD,
        scalar_dim: int = SCALAR_DIM,
        channels: int = 64,
        num_res_blocks: int = 11,
        hidden_dim: int = 256,
    ):
        super().__init__()
        self.in_ch = in_ch
        self.board = board
        self.scalar_dim = scalar_dim
        self.channels = channels

        # Backbone: ConvIn(14→64) + 11 ResBlocks + ConvOut(64→128)
        # Total conv layers: 1 + 11×2 + 1 = 24 layers (13 effective for RF)
        self.conv_in = nn.Sequential(
            nn.Conv2d(in_ch, channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(channels),
            nn.ReLU(inplace=True),
        )
        self.resblocks = nn.Sequential(*[ResBlock(channels) for _ in range(num_res_blocks)])
        self.conv_out = nn.Sequential(
            nn.Conv2d(channels, channels * 2, 3, padding=1, bias=False),
            nn.BatchNorm2d(channels * 2),
            nn.ReLU(inplace=True),
        )

        # GAP → features
        self.gap = nn.AdaptiveAvgPool2d(1)
        backbone_out = channels * 2  # 128

        # Scalar fusion
        self.scalar_fc = nn.Linear(scalar_dim, 64)

        # Shared feature layer
        shared_in = backbone_out + 64  # 128 + 64 = 192
        self.shared = nn.Sequential(
            nn.Linear(shared_in, hidden_dim),
            nn.ReLU(inplace=True),
        )

        # Actor head (policy)
        self.actor = nn.Linear(hidden_dim, TOTAL_ACTION_DIM)

        # Critic head (value)
        self.critic = nn.Linear(hidden_dim, 1)

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
            elif isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=1.0)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
        # Critic head: smaller init for value prediction
        nn.init.orthogonal_(self.critic.weight, gain=0.01)

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass.

        Returns:
            action_logits: (B, 7) raw logits for [move, fire]（v2：item 删除）
            value:         (B, 1)  state value
        """
        # Backbone
        x = obs.float() / 255.0  # normalize uint8 to [0,1]
        x = self.conv_in(x)  # (B, 64, 26, 26)
        x = self.resblocks(x)  # (B, 64, 26, 26)
        x = self.conv_out(x)  # (B, 128, 26, 26)
        x = self.gap(x)  # (B, 128, 1, 1)
        x = x.flatten(1)  # (B, 128)

        # Scalar fusion
        s = F.relu(self.scalar_fc(scalars))  # (B, 64)

        # Shared features
        h = torch.cat([x, s], dim=1)  # (B, 192)
        h = self.shared(h)  # (B, 256)

        # Dual heads
        action_logits = self.actor(h)  # (B, 7)
        value = self.critic(h)  # (B, 1)

        return action_logits, value

    def get_action_and_value(
        self, obs: torch.Tensor, scalars: torch.Tensor, action: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        For training: sample or evaluate actions.

        Returns:
            action:       (B,) sampled action index
            log_prob:     (B,) log probability of the action
            entropy:      (B,) entropy of the policy
            value:        (B,) state value
        """
        logits, value = self.forward(obs, scalars)

        # Split logits into move/fire heads (v2: item 删除)
        move_logits = logits[:, :MOVE_DIM]
        fire_logits = logits[:, MOVE_DIM : MOVE_DIM + FIRE_DIM]

        # Create independent distributions
        move_dist = Categorical(logits=move_logits)
        fire_dist = Categorical(logits=fire_logits)

        # Sample or use provided action
        if action is None:
            move_action = move_dist.sample()
            fire_action = fire_dist.sample()
            action = torch.stack([move_action, fire_action], dim=1)
        else:
            move_action = action[:, 0]
            fire_action = action[:, 1]

        # Log probabilities and entropy
        log_prob = move_dist.log_prob(move_action) + fire_dist.log_prob(fire_action)
        entropy = move_dist.entropy() + fire_dist.entropy()

        return action, log_prob, entropy, value.squeeze(-1)

    @torch.no_grad()
    def get_value(self, obs: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        """Get state value only (for bootstrapping)."""
        _, value = self.forward(obs, scalars)
        return value.squeeze(-1)


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


if __name__ == "__main__":
    model = RLNet()
    n = count_params(model)
    print(f"RLNet params: {n:,} ({n / 1000:.1f}K)")
    print("Receptive field: 27×27 (104% of 26×26)")

    # Test forward pass
    dummy_obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    dummy_sc = torch.zeros(2, SCALAR_DIM)
    logits, value = model(dummy_obs, dummy_sc)
    print(f"action_logits: {logits.shape}")  # (2, 10)
    print(f"value: {value.shape}")  # (2, 1)

    # Test action sampling
    action, log_prob, entropy, value = model.get_action_and_value(dummy_obs, dummy_sc)
    print(f"action: {action.shape}")  # (2, 3)
    print(f"log_prob: {log_prob.shape}")  # (2,)
    print(f"entropy: {entropy.shape}")  # (2,)

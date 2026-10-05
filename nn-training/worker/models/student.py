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

import json
import os

import torch
import torch.nn as nn
import torch.nn.functional as F

from common.schema import BOARD, FIRE_DIM, MOVE_DIM, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM

DEFAULT_H = 64
DEFAULT_D = 8
DEFAULT_HEAD_HIDDEN = 128

# 空间塔冻结值（plan/policy-spatial-head.plan.md §3.3；TS 孪生 src/nn/spatial-tower.ts）。
SPATIAL_TOWER_C = 8
SPATIAL_TOWER_POOL = 4
SPATIAL_TOWER_FC_OUT = 112
SPATIAL_TOWER_FEAT = SPATIAL_TOWER_C * SPATIAL_TOWER_POOL * SPATIAL_TOWER_POOL  # 128


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
      obs:     (B, 16, 26, 26) uint8 — encoder output (v3: 16 channels)
      scalars: (B, 30) float32  (v3: 30 scalars)
      extra:   (B, 9) float32   — POLICY_EXTRA（v4；仅 policy_extra=True 时消费）
    Output: (move_logits, fire_logits)（v2：双头，item 头已删除）.

    v4（plan/policy-spatial-head.plan.md §3）两档新架构（默认关 = 逐字节旧行为）：
      · policy_extra=True（腿 A）：走位/开火头输入 = hidden(128) + extra(9) = 137；
        fc/value 一字不动（critic 输入集不变）。
      · spatial_tower=True（腿 B）：bufA → 1×1 64→8 + ReLU + 4×4 分区均值(128) →
        FC(128→112)；头输入 = tower(112) + scalars(30) + extra(9) = 151。fc/value 仍
        消费 pooled+scalars（不动）。
    """

    def __init__(
        self,
        in_ch: int = OBS_CHANNELS,
        board: int = BOARD,
        scalar_dim: int = SCALAR_DIM,
        h: int = DEFAULT_H,
        d: int = DEFAULT_D,
        head_hidden: int = DEFAULT_HEAD_HIDDEN,
        policy_extra: bool = False,
        spatial_tower: bool = False,
    ):
        super().__init__()
        self.in_ch = in_ch
        self.board = board
        self.scalar_dim = scalar_dim
        self.h = h
        self.d = d
        self.head_hidden = head_hidden
        self.policy_extra = bool(policy_extra)
        self.spatial_tower = bool(spatial_tower)
        if self.spatial_tower and not self.policy_extra:
            # 腿 B 的 151 = tower(112)+scalars(30)+extra(9)；extra 是规格的一部分。
            raise ValueError("spatial_tower 需要 policy_extra=True（头输入 151 含 extra 9）")

        self.stem = nn.Conv2d(in_ch + 2, h, 3, padding=1, bias=True)  # +2 coord channels
        self.blocks = nn.ModuleList([ConvMixerBlock(h) for _ in range(d)])
        self.fc = nn.Linear(h + scalar_dim, head_hidden, bias=True)
        if self.spatial_tower:
            self.spatial_proj = nn.Conv2d(h, SPATIAL_TOWER_C, 1, bias=True)
            self.spatial_fc = nn.Linear(SPATIAL_TOWER_FEAT, SPATIAL_TOWER_FC_OUT, bias=True)
            head_in = SPATIAL_TOWER_FC_OUT + scalar_dim + POLICY_EXTRA_DIM
        elif self.policy_extra:
            head_in = head_hidden + POLICY_EXTRA_DIM
        else:
            head_in = head_hidden
        self.move_head = nn.Linear(head_in, MOVE_DIM, bias=True)
        self.fire_head = nn.Linear(head_in, FIRE_DIM, bias=True)

        self._init_weights()
        # P1-10（2026-09-02）：输入归一化**折进首层权重**。obs 与 coord 通道同为
        # 0..255，对 stem.weight 统一 ×1/255 在数学上等价于 forward 里 input/255
        # ——但权重文件格式不变，TS 运行时 src/nn/infer.ts **零改动**（它不做除法，
        # 权重已含缩放）。历史教训：不归一化导致 trunk 激活 ~千级、策略头 logits
        # ±7600、熵≈0.01，PPO 无法探索（trainer/run_rl.py warm_start_normalize 手工兜底
        # 的根源，plan/python-refactor.md P1-10）。
        with torch.no_grad():
            self.stem.weight.mul_(1.0 / 255.0)

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def arch(self) -> dict:
        d = {
            "kind": "student",
            "in_ch": self.in_ch,
            "board": self.board,
            "scalar_dim": self.scalar_dim,
            "h": self.h,
            "d": self.d,
            "head_hidden": self.head_hidden,
        }
        # v4：只在开启时写入（旧 arch 字典逐字节不变 ⇒ 旧权重/旧消费方零影响）。
        if self.policy_extra:
            d["policyExtra"] = True
        if self.spatial_tower:
            d["spatialTower"] = True
        return d

    def _spatial(self, obs: torch.Tensor) -> torch.Tensor:
        """obs → bufA (B,h,26,26)（GAP 前的空间特征；tower 与 pooled 共用一次前向）。"""
        coords = coord_channels(self.board, obs.device).float().unsqueeze(0)
        x = torch.cat([obs.float(), coords.expand(obs.shape[0], -1, -1, -1)], dim=1)
        x = F.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        return x

    def features(
        self, obs: torch.Tensor, scalars: torch.Tensor, buf: torch.Tensor | None = None
    ) -> torch.Tensor:
        """Shared trunk → hidden (B, head_hidden). Reused by PPO value head.

        v4：`buf` 可传入已算好的 bufA（tower 路径避免同批两次主干前向）。
        """
        x = buf if buf is not None else self._spatial(obs)
        x = x.mean(dim=(2, 3))  # GAP → (B, h)
        x = torch.cat([x, scalars], dim=1)  # (B, h + scalar_dim)
        return F.relu(self.fc(x))

    def _require_extra(self, extra: torch.Tensor | None) -> torch.Tensor:
        if extra is None:
            raise ValueError(
                "student: policy_extra=True 的模型必须喂 POLICY_EXTRA(9)（extra=None 会静默用 0）"
            )
        return extra.float()

    def policy_features(
        self,
        obs: torch.Tensor,
        scalars: torch.Tensor,
        extra: torch.Tensor | None,
        hidden: torch.Tensor | None = None,
        buf: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """走位/开火头的输入（legacy=hidden；腿 A=hidden+extra；腿 B=tower+scalars+extra）。"""
        if self.spatial_tower:
            b = buf if buf is not None else self._spatial(obs)
            z = F.relu(self.spatial_proj(b))
            p = F.adaptive_avg_pool2d(z, SPATIAL_TOWER_POOL).flatten(1)  # (B,128)
            p = self.spatial_fc(p)  # (B,112)（FC 128→112 是塔的一部分，plan §3.3）
            return torch.cat([p, scalars, self._require_extra(extra)], dim=1)
        h = hidden if hidden is not None else self.features(obs, scalars, buf)
        if self.policy_extra:
            return torch.cat([h, self._require_extra(extra)], dim=1)
        return h

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor, extra: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, ...]:
        """obs: (B,16,26,26) u1; scalars: (B,30) f4; extra: (B,9) f4（policy_extra 时必给）
        → (move_logits, fire_logits)。

        返回类型刻意写成变长 tuple：子类（PPOStudent / IntentNet / GoalNet）会在
        尾部追加 value 等头，固定 2 元组会让每处 override 都违反 LSP。运行时不变。
        """
        if self.spatial_tower:
            buf = self._spatial(obs)
            h = self.features(obs, scalars, buf)
            pf = self.policy_features(obs, scalars, extra, hidden=h, buf=buf)
        else:
            h = self.features(obs, scalars)
            pf = self.policy_features(obs, scalars, extra, hidden=h)
        return self.move_head(pf), self.fire_head(pf)


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
        self, obs: torch.Tensor, scalars: torch.Tensor, extra: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        if self.spatial_tower:
            buf = self._spatial(obs)
            h = self.features(obs, scalars, buf)
            pf = self.policy_features(obs, scalars, extra, hidden=h, buf=buf)
        else:
            h = self.features(obs, scalars)
            pf = self.policy_features(obs, scalars, extra, hidden=h)
        return self.move_head(pf), self.fire_head(pf), self.value_head(h)

    @torch.no_grad()
    def predict(
        self,
        obs: torch.Tensor,
        scalars: torch.Tensor | None = None,
        extra: torch.Tensor | None = None,
    ):
        """Inference helper: returns softmax-prob dicts (mirrors TS infer)."""
        self.eval()
        if scalars is None:
            scalars = torch.zeros(obs.shape[0], self.scalar_dim)
        m, f, _v = self.forward(obs, scalars, extra)
        return (
            torch.softmax(m, dim=-1),
            torch.softmax(f, dim=-1),
        )


def param_count(model: nn.Module) -> int:
    return sum(int(p.numel()) for p in model.parameters())


def export_student_golden(
    path: str,
    h: int = DEFAULT_H,
    d: int = DEFAULT_D,
    seed: int = 20260903,
    policy_extra: bool = False,
    spatial_tower: bool = False,
) -> None:
    """轴 2 parity golden（PPOStudent 生产路径，kind='student'）。

    输出 JSON：{ format:"student-golden", version, h, d, head_hidden, seed,
                 obs, scalars, [extra,] moveLogits:[5], fireLogits:[2], valueLogits:[1],
                 [policyExtra, spatialTower,] params（stem/blocks/fc/move_head/fire_head/value_head
                 [+spatial_proj/spatial_fc]）}
    TS 端 buildModelFromText(arch.kind='student', h, d[, policyExtra/spatialTower]) →
    forward() 对比三头。

    为什么需要（2026-09-03 审计）：goal/intent golden 只覆盖 StudentNet 主干+专用头，
    从不触碰 **per-tick 策略头（move_head/fire_head + 128 宽 value_head）**——而这是
    export-rl-rollout.ts / s5-open20 活路径。任一侧改这些头或主干都会在此变红。

    v4（plan/policy-spatial-head.plan.md）：policy_extra/spatial_tower 两档新架构各自的
    golden 由本函数导出（同一 seed 族）；旧调用（不带 flag）输出逐字节不变。
    """
    torch.manual_seed(seed)
    rng = torch.Generator().manual_seed(seed)
    obs = torch.randint(0, 256, (1, OBS_CHANNELS, BOARD, BOARD), generator=rng, dtype=torch.uint8)
    sc = (torch.rand(1, SCALAR_DIM, generator=rng) - 0.5) * 4
    ex = (torch.rand(1, POLICY_EXTRA_DIM, generator=rng) - 0.25) * 1.5  # 覆盖哨兵 1.5 附近

    torch.manual_seed(seed + 1)
    m = PPOStudent(h=h, d=d, policy_extra=policy_extra, spatial_tower=spatial_tower).eval()
    with torch.no_grad():
        mv, fr, v = m(obs, sc, ex if policy_extra else None)  # PPOStudent: (move, fire, value)

    from worker.data.weights_io import tensor_to_b64

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
    if policy_extra:
        golden["policyExtra"] = True
        golden["extra"] = [float(v) for v in ex.flatten().tolist()]
    if spatial_tower:
        golden["spatialTower"] = True
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(golden, f)
    print(
        f"student golden written: {path} (h={h} d={d} head_hidden={m.head_hidden} "
        f"policyExtra={policy_extra} spatialTower={spatial_tower} params={len(params)})"
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--golden", metavar="OUT", help="PPOStudent golden 导出：固定权重+输入 → move/fire/value logits JSON"
    )
    ap.add_argument("--h", type=int, default=DEFAULT_H)
    ap.add_argument("--d", type=int, default=DEFAULT_D)
    ap.add_argument("--golden-seed", type=int, default=20260903)
    ap.add_argument("--policy-extra", action="store_true", help="v4 腿 A 档（头 137）golden")
    ap.add_argument("--spatial-tower", action="store_true", help="v4 腿 B 档（塔 + 头 151）golden")
    args = ap.parse_args()

    if args.golden:
        export_student_golden(
            args.golden,
            args.h,
            args.d,
            args.golden_seed,
            policy_extra=args.policy_extra,
            spatial_tower=args.spatial_tower,
        )
        raise SystemExit(0)

    m = StudentNet(
        policy_extra=args.policy_extra, spatial_tower=args.spatial_tower
    )
    n = param_count(m)
    print(f"StudentNet params: {n} (~{n / 1000:.1f}K)  budget<=200K: {n <= 200_000}")
    dummy_obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    dummy_sc = torch.zeros(2, SCALAR_DIM)
    dummy_ex = torch.zeros(2, POLICY_EXTRA_DIM) if args.policy_extra else None
    mv, fr = m(dummy_obs, dummy_sc, dummy_ex)
    print("move", tuple(mv.shape), "fire", tuple(fr.shape))
    print("arch:", m.arch())

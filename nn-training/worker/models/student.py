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

from common.cli import house_parser
from common.schema import (
    BOARD,
    FIRE3_DIM,
    FIRE_DIM,
    HOLD_DIM,
    MOVE4_DIM,
    MOVE_DIM,
    OBS_CHANNELS,
    POLICY_EXTRA_DIM,
    SCALAR_DIM,
)
from worker.models._common import param_count as param_count

# 架构默认档 / 空间塔冻结值 / 架构元数据面 —— 与 Paddle 双生**同一份**（`worker/models/arch_meta.py`）。
# 这里的名字是**转发名**：`tests/worker/test_policy_new_rms.py` / `test_value_anchor.py` 仍按
# 老位置 `from worker.models.student import SPATIAL_TOWER_FC_OUT` 取值（名字是契约，位置不是）。
from worker.models.arch_meta import (
    DEFAULT_D,
    DEFAULT_H,
    DEFAULT_HEAD_HIDDEN,
    SPATIAL_TOWER_C,
    SPATIAL_TOWER_FC_OUT,
    SPATIAL_TOWER_FEAT,
    SPATIAL_TOWER_POOL,
    StudentArchMixin,
)


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


class StudentNet(StudentArchMixin, nn.Module):
    """
    CoordConv-ConvMixer-Lite student (plan §4.3), BN-free.

    Input:
      obs:     (B, 16, 26, 26) uint8 — encoder output (v3+: 16 channels)
      scalars: (B, 40) float32  (v5: 40 scalars；v3 为 30)
      extra:   (B, 37) float32   — POLICY_EXTRA（v5: 37；仅 policy_extra=True 时消费）
    Output: (move_logits, fire_logits)（v2：双头，item 头已删除）.

    v4（plan/policy-spatial-head.plan.md §3）两档架构（默认关 = legacy）：
      · policy_extra=True（腿 A）：走位/开火头输入 = hidden(128) + extra(37) = 165；
      · spatial_tower=True（腿 B）：bufA → 1×1 64→8 + ReLU + 4×4 分区均值(128) →
        FC(128→112)；头输入 = tower(112) + scalars(40) + extra(37) = 189。
    v5（plan/obs-v5-vector-gap.plan.md §2.6）：上述 165/189 也是 `PPOStudent.value_head`
    的输入宽度（critic 与策略吃同一份 `pf`）；legacy 档 value 仍是 128 = head_hidden。
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
        action_v6: bool = False,
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
        # B1 v6: three heads hold(2)/move(4)/fire(3). Opt-in so the still-current
        # 5/2 BC/PPO corpora keep their exact head shapes; the switch travels with
        # the exported weights (`hold_head.*` presence), same as TS.
        self.action_v6 = bool(action_v6)
        self.move_dim = MOVE4_DIM if self.action_v6 else MOVE_DIM
        self.fire_dim = FIRE3_DIM if self.action_v6 else FIRE_DIM
        if self.spatial_tower and not self.policy_extra:
            # 腿 B 的 189 = tower(112)+scalars(40)+extra(37)；extra 是规格的一部分。
            raise ValueError(
                f"spatial_tower 需要 policy_extra=True（头输入含 extra({POLICY_EXTRA_DIM})）"
            )

        self.stem = nn.Conv2d(in_ch + 2, h, 3, padding=1, bias=True)  # +2 coord channels
        self.blocks = nn.ModuleList([ConvMixerBlock(h) for _ in range(d)])
        self.fc = nn.Linear(h + scalar_dim, head_hidden, bias=True)
        if self.spatial_tower:
            self.spatial_proj = nn.Conv2d(h, SPATIAL_TOWER_C, 1, bias=True)
            self.spatial_fc = nn.Linear(SPATIAL_TOWER_FEAT, SPATIAL_TOWER_FC_OUT, bias=True)
        head_in = self.policy_input_dim
        self.move_head = nn.Linear(head_in, self.move_dim, bias=True)
        self.fire_head = nn.Linear(head_in, self.fire_dim, bias=True)
        self.hold_head = nn.Linear(head_in, HOLD_DIM, bias=True) if self.action_v6 else None

        self._init_weights()
        # P1-10（2026-09-02）：输入归一化**折进首层权重**。obs 与 coord 通道同为
        # 0..255，对 stem.weight 统一 ×1/255 在数学上等价于 forward 里 input/255
        # ——但权重文件格式不变，TS 运行时 src/nn/infer.ts **零改动**（它不做除法，
        # 权重已含缩放）。历史教训：不归一化导致 trunk 激活 ~千级、策略头 logits
        # ±7600、熵≈0.01，PPO 无法探索（trainer/run_rl.py warm_start_normalize 手工兜底
        # 的根源，plan/python-refactor.md P1-10）。
        with torch.no_grad():
            self.stem.weight.mul_(1.0 / 255.0)

    # 架构元数据面（`arch` / `policy_input_dim` / `value_anchor_slices`）在
    # `worker/models/arch_meta.py`：与 Paddle 双生共用同一份实现（本类经 `StudentArchMixin` 继承）。

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                nn.init.kaiming_uniform_(m.weight, nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

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
                f"student: policy_extra=True 的模型必须喂 POLICY_EXTRA({POLICY_EXTRA_DIM})"
                "（extra=None 会静默用 0）"
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
        if self.action_v6:
            # B1 v6: `(move, fire, hold)`. Legacy callers never construct this
            # shape, so their 2-tuple contract is untouched.
            hold_head = self.hold_head
            assert hold_head is not None  # action_v6=True always builds it
            return self.move_head(pf), self.fire_head(pf), hold_head(pf)
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
        # obs-v5（plan/obs-v5-vector-gap.plan.md §2.6）：critic 吃与策略头**同一份** `pf`。
        # 旧稿只吃 `head_hidden`（128）—— critic 与策略不在同一坐标系，正是本 plan 要修的根因
        # （DECISIONS §2026-10-12-goalnn-obs-v5-critic-superset 已推翻「critic 输入集不动」）。
        self.value_head = nn.Linear(self.policy_input_dim, 1)
        self._init_value_head()

    def _init_value_head(self) -> None:
        """value 头初始化：小尺度 `normal_(0, 0.01)` + 零偏置（plan §2.6）。

        为何不用 `_init_weights` 的 kaiming：obs-v5 的 value 头**新吃 37 列**
        （extra / scalars 尾巴），而 warm start 会把已训过的前几列拷进来——kaiming 的 √2
        gain 会把新列抬到与老列同量级，起点上 critic 就被新列的噪声主导。0.01 量级
        让新列近似「关闭」起步，要不要用交给训练自己学（不预剪，plan §2.6）。
        转出脚本（`scripts/init_spatial_leg.py`）对**部分列**重用同一尺度。
        """
        with torch.no_grad():
            nn.init.normal_(self.value_head.weight, mean=0.0, std=0.01)
            nn.init.zeros_(self.value_head.bias)

    def forward(
        self, obs: torch.Tensor, scalars: torch.Tensor, extra: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, ...]:
        if self.spatial_tower:
            buf = self._spatial(obs)
            h = self.features(obs, scalars, buf)
            pf = self.policy_features(obs, scalars, extra, hidden=h, buf=buf)
        else:
            h = self.features(obs, scalars)
            pf = self.policy_features(obs, scalars, extra, hidden=h)
        if self.action_v6:
            # 4-tuple: old PPO update code unpacks 3 and fails loudly on a v6
            # model — intentional until the rollout/shard migration lands
            # (plan §4-B1: no silent 5/2 fallback).
            hold_head = self.hold_head
            assert hold_head is not None
            return self.move_head(pf), self.fire_head(pf), hold_head(pf), self.value_head(pf)
        return self.move_head(pf), self.fire_head(pf), self.value_head(pf)

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
        out = self.forward(obs, scalars, extra)
        m, f = out[0], out[1]
        return (
            torch.softmax(m, dim=-1),
            torch.softmax(f, dim=-1),
        )


def export_student_golden(
    path: str,
    h: int = DEFAULT_H,
    d: int = DEFAULT_D,
    seed: int = 20260903,
    policy_extra: bool = False,
    spatial_tower: bool = False,
    action_v6: bool = False,
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
    m = PPOStudent(
        h=h, d=d, policy_extra=policy_extra, spatial_tower=spatial_tower, action_v6=action_v6
    ).eval()
    with torch.no_grad():
        out = m(obs, sc, ex if policy_extra else None)
    # PPOStudent: legacy (move, fire, value) / v6 (move, fire, hold, value).
    mv, fr, v = out[0], out[1], out[-1]

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
    if action_v6:
        golden["actionV6"] = True
        golden["holdLogits"] = [float(v) for v in out[2].flatten().tolist()]
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

    ap = house_parser()
    ap.add_argument(
        "--golden", metavar="OUT", help="PPOStudent golden 导出：固定权重+输入 → move/fire/value logits JSON"
    )
    ap.add_argument("--h", type=int, default=DEFAULT_H)
    ap.add_argument("--d", type=int, default=DEFAULT_D)
    ap.add_argument("--golden-seed", type=int, default=20260903)
    ap.add_argument("--policy-extra", action="store_true", help="腿 A 档（头 + extra 37 = 165）golden")
    ap.add_argument(
        "--spatial-tower", action="store_true", help="腿 B 档（塔 + scalars + extra = 189）golden"
    )
    ap.add_argument(
        "--action-v6", action="store_true", help="B1 v6 三头（hold2/move4/fire3）golden"
    )
    args = ap.parse_args()

    if args.golden:
        export_student_golden(
            args.golden,
            args.h,
            args.d,
            args.golden_seed,
            policy_extra=args.policy_extra,
            spatial_tower=args.spatial_tower,
            action_v6=args.action_v6,
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

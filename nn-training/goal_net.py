"""
goal_net.py — Goal-Space 策略网（plan/Goal-Space-Policy-Rebuild.md §8 / 任务卡 T7）。

复用 StudentNet 主干（CoordConv-ConvMixer-Lite，BN-free，schema v2）+ **三头**（§8.3.0，
"有监督 ∧ 有消费方才入网"判据——target/T/K 头按 §8.3.0b 被砍，不得回加）：
  spatial(obs, scalars) -> (B, h, 26, 26)   # bufA：stem + d×ConvMixer + residual，GAP 前
  → goal_conv = Conv2d(h, 1, 1) → goal 热图 (B, 676)          # 65 参数（§8.4）
  hidden(128) + inject(9) → (B, 137)
  → engage_head(137→2)                                       # §8.3.1 本窗口要不要主动交战
  → value_head(137→1)（with_value=True 的 RL 变体）           # GAE/T9a T9

inject 9 维语义（§8.1.1，宽度保持 9 零形状 churn；与 TS 侧 src/nn/goal-inject.ts 逐维一致）：
  [0] prevGoalRow/26   [1] prevGoalCol/26   [2] min(duration,300)/300
  [3] min(switches,10)/10   [4] arrived?1:0   [5..8] 保留恒 0

被删的 intent 三头（intent/enemy/anchor）不在本网：enemy/anchor 无监督无消费方（R3/§8.3.0），
intent 头被 goal-space 整体替换。TS 侧 StudentModel 保留 intent 头加载能力仅为旧
checkpoint（it38 重评 §14.3）兼容，goal 权重 JSON 不含它们。

Params（h=64/d=8）：主干 67.5K + goal_conv 65 + engage 276 + value 138 ≈ 67.9K。
MAdds：主干 ~37M + goal 头 43,264（0.115%，§16.2）。
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

import torch
import torch.nn as nn

from student_model import StudentNet, coord_channels
from schema import OBS_CHANNELS, BOARD, SCALAR_DIM

GOAL_HEATMAP_DIM = BOARD * BOARD  # 676（动作空间 = 坦克顶点热图；§9.4.0 合法顶点 25×25=625）
ENGAGE_DIM = 2
INJECT_DIM = 9  # 与 intent 同宽，语义重定义（§8.1.1）
GOAL_INJECT_RESERVED_FROM = 5  # 维 5–8 保留恒 0


class GoalNet(StudentNet):
    """StudentNet 主干 + goal 热图头 + engage 头（+可选 value 头）。"""

    def __init__(self, with_value: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.goal_conv = nn.Conv2d(self.h, 1, 1, bias=True)  # bufA → 26×26 热图
        head_in = self.head_hidden + INJECT_DIM  # 137
        self.engage_head = nn.Linear(head_in, ENGAGE_DIM)
        self.value_head = nn.Linear(head_in, 1) if with_value else None

    def spatial(self, obs: torch.Tensor, scalars: torch.Tensor):
        """主干到 GAP 前：(B,h,26,26) 空间特征 + (B,128) FC 隐藏（T7 bufA 接入点 §8.2）。"""
        coords = coord_channels(self.board, obs.device).float().unsqueeze(0)
        x = torch.cat([obs.float(), coords.expand(obs.shape[0], -1, -1, -1)], dim=1)
        x = nn.functional.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        sp = x  # (B, h, 26, 26) — bufA
        pooled = x.mean(dim=(2, 3))
        h = nn.functional.relu(self.fc(torch.cat([pooled, scalars], dim=1)))
        return sp, h

    def forward(
        self,
        obs: torch.Tensor,
        scalars: torch.Tensor,
        inject: torch.Tensor | None = None,
    ):
        """→ (goal_logits (B,676), engage_logits (B,2))；with_value 时第三返回 value (B,1)。
        inject=None 时置 0（维 5–8 恒 0 由写入方保证）。"""
        sp, h = self.spatial(obs, scalars)
        if inject is None:
            inject = torch.zeros(h.shape[0], self.inject_dim)
        hi = torch.cat([h, inject.float()], dim=1)  # (B,137)
        goal = self.goal_conv(sp).flatten(1)  # (B,676)
        engage = self.engage_head(hi)
        if self.value_head is not None:
            return goal, engage, self.value_head(hi)
        return goal, engage

    def forward_rl(
        self, obs: torch.Tensor, scalars: torch.Tensor, inject: torch.Tensor
    ):
        """PPO 变体（value 必须看到承诺状态——与 intent_net.forward_rl 同理由）。"""
        assert self.value_head is not None, "forward_rl requires with_value=True"
        goal, engage, value = self.forward(obs, scalars, inject)
        return goal, engage, value

    def arch(self) -> dict:
        d = super().arch()
        d.update(
            {
                "kind": "goal",
                "goalHeatmap": GOAL_HEATMAP_DIM,
                "engageDim": ENGAGE_DIM,
                "injectDim": INJECT_DIM,
                "valueHead": self.value_head is not None,
            }
        )
        return d


def export_goal_weights(model: GoalNet, out_path: str) -> None:
    """weights_io 同构 JSON 导出（预注册 #8 校验内置：主干键名/shape 与 StudentNet 一致）。"""
    from weights_io import save_weights_json

    sd = model.state_dict()
    ref = StudentNet(h=model.h, d=model.d).state_dict()
    missing = [k for k in ref if k not in sd]
    shape_mismatch = [k for k in ref if k in sd and tuple(sd[k].shape) != tuple(ref[k].shape)]
    if missing or shape_mismatch:
        raise AssertionError(
            f"export_goal_weights: trunk diverges from StudentNet — "
            f"missing={missing} shapeMismatch={shape_mismatch}"
        )
    extra = [
        k
        for k in sd
        if not k.startswith(("goal_conv.", "engage_head.", "value_head.")) and k not in ref
    ]
    if extra:
        raise AssertionError(f"export_goal_weights: unexpected extra keys {extra}")
    save_weights_json(model, out_path, extra_meta={"goalHead": True})


def load_goal_weights(model: GoalNet, weights_path: str) -> None:
    """加载导出 JSON（主干缺失可接受——transfer init 只给主干；goal/engage 头必须齐全）。"""
    from weights_io import load_weights_json

    _meta, data = load_weights_json(weights_path)
    sd = model.state_dict()
    for head in ("goal_conv", "engage_head"):
        if not any(k.startswith(head + ".") for k in data):
            raise AssertionError(f"load_goal_weights: missing {head} in {weights_path}")
    filtered = OrderedDict()
    for k, v in data.items():
        if k in sd:
            assert tuple(v.shape) == tuple(sd[k].shape), (
                f"shape mismatch {k}: {tuple(v.shape)} vs {tuple(sd[k].shape)}"
            )
            filtered[k] = v
    model.load_state_dict(filtered, strict=False)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", metavar="OUT", help="T7 golden 导出：固定权重+输入 → 期望头 logits JSON")
    ap.add_argument("--h", type=int, default=64)
    ap.add_argument("--d", type=int, default=8)
    ap.add_argument("--golden-seed", type=int, default=20260829)
    args = ap.parse_args()

    if args.golden:
        export_golden(args.golden, args.h, args.d, args.golden_seed)
        return

    m = GoalNet(h=args.h, d=args.d)
    n = sum(int(p.numel()) for p in m.parameters())
    print(f"GoalNet params: {n} (~{n/1000:.1f}K)")
    obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    sc = torch.zeros(2, SCALAR_DIM)
    inj = torch.zeros(2, INJECT_DIM)
    g, e = m(obs, sc, inj)
    print("goal", tuple(g.shape), "engage", tuple(e.shape))
    export_goal_weights(m, "tmp/_goal_net_weights.json")
    print("export roundtrip ok")


def export_golden(path: str, h: int, d: int, seed: int) -> None:
    """T7：固定 seed 瘦身 GoalNet → 固定输入 → 写期望头 logits JSON。

    输出 JSON：{ format:"goal-golden", h, d, seed, obs, scalars, inject,
                 goalLogits:[676], engageLogits:[2], valueLogits:[1],
                 params（stem/blocks/fc/goal_conv/engage_head/value_head）}
    TS 端 buildGoalModelFromJson 只需 params（arch.kind=goal,h,d）→ goalForward 对比。
    """
    torch.manual_seed(seed)
    rng = torch.Generator().manual_seed(seed)
    obs = torch.randint(0, 256, (1, OBS_CHANNELS, BOARD, BOARD), generator=rng, dtype=torch.uint8)
    sc = (torch.rand(1, SCALAR_DIM, generator=rng) - 0.5) * 4
    # inject 覆盖 §8.1.1 全部活跃维（0–4），保留维 5–8 置 0（TS 侧断言恒 0）。
    inj = torch.zeros(1, INJECT_DIM)
    inj[0, 0] = 12 / 26  # prevGoalRow
    inj[0, 1] = 9 / 26  # prevGoalCol
    inj[0, 2] = 187 / 300  # duration
    inj[0, 3] = 4 / 10  # switches
    inj[0, 4] = 1.0  # arrived

    torch.manual_seed(seed + 1)
    m = GoalNet(h=h, d=d, with_value=True).eval()
    with torch.no_grad():
        g_log, e_log, v_log = m.forward_rl(obs, sc, inj)

    from weights_io import tensor_to_b64, OBS_SCHEMA_MAJOR

    params = {}
    for name, p in m.state_dict().items():
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
    golden = {
        "format": "goal-golden",
        "version": 1,
        "schema_major": OBS_SCHEMA_MAJOR,
        "h": h,
        "d": d,
        "seed": seed,
        "obs": [int(v) for v in obs.flatten().tolist()],
        "scalars": [float(v) for v in sc.flatten().tolist()],
        "inject": [float(v) for v in inj.flatten().tolist()],
        "goalLogits": [float(v) for v in g_log.flatten().tolist()],
        "engageLogits": [float(v) for v in e_log.flatten().tolist()],
        "valueLogits": [float(v) for v in v_log.flatten().tolist()],
        "params": params,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(golden, f)
    print(f"goal golden written: {path} (h={h} d={d} params={len(params)})")


if __name__ == "__main__":
    main()

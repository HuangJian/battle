"""
intent_net.py — M4 意图策略网定义（plan/Intent-Policy-NN-Plan.md §4，M0b gate 后启用）。

复用 StudentNet 主干（CoordConv-ConvMixer-Lite，BN-free，schema v2）+ **三头**：
  features(obs, scalars) -> (B, 128) FC 隐藏
  → concat 运行时注入（9 维：prev-intent one-hot(8) + duration(1)）→ (B, 137)
  → ① intent 头 8   ② 目标敌头 5  ③ 目标锚头 16   (+ value 头 1，M8 启用)

注入位置 = FC 后 128→137（§4.1/§4.2 钉死，主干+FC 权重可原样迁移自既有
StudentNet——预注册 #8：导出时校验 stem/ConvMixer/FC shape 与 student_model
完全一致）。非激活头不产梯度由训练侧 mask（vocab.ACTIVATION_MATRIX 镜像）。

Params（h=64/d=8）：主干 67.5K + 三头 (137→8=1104 / 137→5=690 / 137→16=2208)
≈ 70.6K。存 torch 权重仅存【权衡层】；StudentNet 主干部分复用既有导出键名。
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict

import torch
import torch.nn as nn
from models.student import StudentNet

INTENT_DIM = 8
ENEMY_HEAD_DIM = 5  # none + e0..e3
ANCHOR_HEAD_DIM = 16  # role 槽位（vocab.ANCHOR_ROLE_IDS）
INJECT_DIM = 9  # one-hot(8) + duration(1)


class IntentNet(StudentNet):
    """StudentNet 主干 + 三意图头（+可选 value 头，M8）。"""

    def __init__(self, inject: bool = False, with_value: bool = False, **kwargs):
        self.inject = inject
        self.inject_dim = INJECT_DIM
        super().__init__(**kwargs)
        head_in = self.head_hidden + INJECT_DIM  # 137
        self.intent_head = nn.Linear(head_in, INTENT_DIM)
        self.enemy_head = nn.Linear(head_in, ENEMY_HEAD_DIM)
        self.anchor_head = nn.Linear(head_in, ANCHOR_HEAD_DIM)
        self.value_head = nn.Linear(head_in, 1) if with_value else None

    def forward(
        self,
        obs: torch.Tensor,
        scalars: torch.Tensor,
        inject: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """obs:(B,14,26,26)u1 scalars:(B,19)f4 inject:(B,9)f4
        → (intent_logits, enemy_logits, anchor_logits). inject=None 时置 0。"""
        h = self.features(obs, scalars)
        if inject is None:
            inject = torch.zeros(h.shape[0], self.inject_dim)
        h = torch.cat([h, inject.float()], dim=1)  # (B,137)
        return self.intent_head(h), self.enemy_head(h), self.anchor_head(h)

    def forward_rl(
        self,
        obs: torch.Tensor,
        scalars: torch.Tensor,
        inject: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """M8（P1-5②）：value 头与三头并列消费同一 137 隐藏（128+9 注入）——
        value 必须看到承诺状态（同 obs 不同意图龄的 value 估计才不偏）。inject 必传。
        → (intent, enemy, anchor, value)。仅 when with_value（ppo_intent 用）。"""
        h = self.features(obs, scalars)
        h = torch.cat([h, inject.float()], dim=1)  # (B,137)
        v = self.value_head(h) if self.value_head is not None else torch.zeros(h.shape[0], 1)
        return self.intent_head(h), self.enemy_head(h), self.anchor_head(h), v

    def arch(self) -> dict:
        d = super().arch()
        d["intent"] = {k: v for k, v in d.items() if k != "kind"}
        d.update(
            {
                "kind": "intent",
                "intentDim": INTENT_DIM,
                "enemyDim": ENEMY_HEAD_DIM,
                "anchorDim": ANCHOR_HEAD_DIM,
                "injectDim": INJECT_DIM,
            }
        )
        return d


def export_intent_weights(model: IntentNet, out_path: str) -> None:
    """weights_io 同构 JSON 导出（预注册 #8 校验内置）。

    stem/blocks/fc 键与 StudentNet 完全同名同 shape（迁移可复用既有 bc 权重做
    transfer init——P1-3 双臂的 transfer 臂）。heads 以 intent_head.* 等键名导出。
    save_weights_json 自动写 model.arch() 并支持 extra_meta。
    """
    from data.weights_io import save_weights_json

    sd = model.state_dict()
    ref = StudentNet().state_dict()
    missing = [k for k in ref if k not in sd]
    shape_mismatch = [k for k in ref if k in sd and tuple(sd[k].shape) != tuple(ref[k].shape)]
    if missing or shape_mismatch:
        raise AssertionError(
            f"export_intent_weights: trunk diverges from StudentNet — "
            f"missing={missing} shapeMismatch={shape_mismatch}"
        )
    extra = [
        k
        for k in sd
        if not k.startswith(("intent_head.", "enemy_head.", "anchor_head.", "value_head."))
        and k not in ref
    ]
    if extra:
        raise AssertionError(f"export_intent_weights: unexpected extra keys {extra}")
    save_weights_json(model, out_path, extra_meta={"intentHead": True})


def load_intent_weights(model: IntentNet, weights_path: str) -> None:
    """加载导出 JSON（主干部分缺失时——例如 transfer 只给主干——也接受，仅校验
    存在的键 shape 一致；heads 必须齐全）。"""
    from data.weights_io import load_weights_json

    _meta, data = load_weights_json(weights_path)
    sd = model.state_dict()
    missing_heads = [
        h
        for h in ("intent_head", "enemy_head", "anchor_head")
        if not any(k.startswith(h + ".") for k in data)
    ]
    if missing_heads:
        raise AssertionError(
            f"load_intent_weights: missing heads {missing_heads} in {weights_path}"
        )
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
    ap.add_argument(
        "--golden", metavar="OUT", help="P3-4 golden 导出：固定权重+输入 → 期望三头 logits JSON"
    )
    ap.add_argument("--h", type=int, default=64)
    ap.add_argument("--d", type=int, default=8)
    ap.add_argument("--golden-seed", type=int, default=20260826)
    args = ap.parse_args()

    if args.golden:
        export_golden(args.golden, args.h, args.d, args.golden_seed)
        return

    m = IntentNet(h=args.h, d=args.d)
    n = sum(int(p.numel()) for p in m.parameters())
    print(f"IntentNet params: {n} (~{n / 1000:.1f}K)")
    obs = torch.zeros(2, 14, 26, 26, dtype=torch.uint8)
    sc = torch.zeros(2, 19)
    inj = torch.zeros(2, INJECT_DIM)
    i, e, a = m(obs, sc, inj)
    print("intent", tuple(i.shape), "enemy", tuple(e.shape), "anchor", tuple(a.shape))
    export_intent_weights(m, "tmp/_intent_net_weights.json")
    print("export roundtrip ok")


def export_golden(path: str, h: int, d: int, seed: int) -> None:
    """P3-4：固定 seed 初始化瘦身 IntentNet → 固定输入 → 写期望 logits。

    输出 JSON 顶层结构：
      { h, d, seed,
        obs:     [14*26*26]  (u1, 0..255),
        scalars: [19]        (f4),
        inject:  [9]         (f4),
        intentLogits: [8], enemyLogits: [5], anchorLogits: [16],
        valueLogits: [1]  (M8：value 头 137→1 与三头并列，with_value=True),
        ...weights_io 的 params 字段（stem/blocks/fc/intent_head/enemy_head/anchor_head/value_head）
      }
    TS 端 buildIntentModelFromJson 只需 params（arch.kind=intent,h,d）→ intentForward 对比 logits。
    """
    torch.manual_seed(seed)
    # 输入：确定性伪随机 obs（0..255）+ 非零 scalars/inject（覆盖注入路径）。
    rng = torch.Generator().manual_seed(seed)
    obs = torch.randint(0, 256, (1, 14, 26, 26), generator=rng, dtype=torch.uint8)
    sc = (torch.rand(1, 19, generator=rng) - 0.5) * 4  # 有正有负
    inj = torch.rand(1, INJECT_DIM, generator=rng)  # 非 one-hot 更严（时长非整数路径）
    inj[0, 0] = 1.0  # prev 类 0 热

    torch.manual_seed(seed + 1)
    m = IntentNet(h=h, d=d, with_value=True).eval()
    with torch.no_grad():
        i_log, e_log, a_log, v_log = m.forward_rl(obs, sc, inj)

    from data.weights_io import OBS_SCHEMA_MAJOR, tensor_to_b64

    params = {}
    for name, p in m.state_dict().items():
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
    golden = {
        "format": "intent-golden",
        "version": 2,
        "schema_major": OBS_SCHEMA_MAJOR,
        "h": h,
        "d": d,
        "seed": seed,
        "obs": [int(v) for v in obs.flatten().tolist()],
        "scalars": [float(v) for v in sc.flatten().tolist()],
        "inject": [float(v) for v in inj.flatten().tolist()],
        "intentLogits": [float(v) for v in i_log.flatten().tolist()],
        "enemyLogits": [float(v) for v in e_log.flatten().tolist()],
        "anchorLogits": [float(v) for v in a_log.flatten().tolist()],
        "valueLogits": [float(v) for v in v_log.flatten().tolist()],
        "params": params,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(golden, f)
    print(f"golden written: {path} (h={h} d={d} params={len(params)})")


if __name__ == "__main__":
    main()

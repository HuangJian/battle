"""test_ppo_common.py — ppo_common.py 共享 PPO 基础设施常驻回归测试。

覆盖（无真实训练、不碰节点）：
  1) compute_gae(dt=None) 定长路径 == 手算 GAE；dt=全 1 变步长路径与定长逐字节一致
     （P1-7k3 的 Δt≡1 退化保证，先例 test_ppo_intent.py::test_dt1_degradation）；
  2) dt≠1 时变步长确实产生差异（不恒等于定长）；
  3) masked_logsoftmax：无效位压 -inf、有效位 = log_softmax；
  4) cat_logprob / cat_entropy 基本性质；
  5) chunk_episodes：按 mb 切块（2026-09-23 起全部恰为 mb，尾部 n%mb 丢弃）、键保持；
  6) checkpoint RNG 往返：_pack/_unpack 后 np.random 状态精确重建；
  7) discover_shards / load_shard_fields：marker 过滤 + 零拷贝 astype 字段表。
  8) demo_index：复用**同一张设备缓冲**（XLA 图签名恒定）、数值与朴素 numpy 索引逐位相同、
     buf=None（非 demo 路径）原样返回——原 test_xla_step_diag.py::TestDemoIndexBuffer，
     2026-09-26 随「该文件的 torch 用例归位到 ppo/common 的家」移入。

运行（经统一启动器进入 venv）：
  python test_ppo_common.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import numpy.typing as npt
import torch

from schema import OBS_CHANNELS, SCALAR_DIM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ppo.common as ppo_common

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def test_masked_logsoftmax() -> None:
    logits = torch.tensor([[1.0, 2.0, 3.0], [0.5, -1.0, 4.0]])
    mask = torch.tensor([[1, 1, 0], [0, 1, 1]])
    out = ppo_common.masked_logsoftmax(logits, mask)
    # 无效位被压到极负（原实现用 -1e9 近似 -inf，与 ppo.py 历史行为一致）
    check(bool((out[0, 2] < -1e6).item()), "无效类 logp ≈ -inf（极负）")
    # 有效位 = 仅在有效子集上的 log_softmax
    exp_0 = torch.log_softmax(logits[0, :2], dim=-1)
    check(torch.allclose(out[0, :2], exp_0, atol=1e-6), "有效位 logp = log_softmax(有效子集)")
    # 概率和（exp）在有效位上 = 1
    probs = out.exp()
    check(
        bool(torch.isclose(probs[0, :2].sum(), torch.tensor(1.0), atol=1e-5).item()),
        "有效位概率和为 1",
    )


def test_cat_logprob_entropy() -> None:
    logp = torch.tensor([[0.1, 0.5, 0.4], [0.2, 0.3, 0.5]])
    a = torch.tensor([1, 2])
    got = ppo_common.cat_logprob(a, logp)
    check(torch.allclose(got, torch.tensor([0.5, 0.5])), "cat_logprob gather 正确")
    ent = ppo_common.cat_entropy(logp)
    manual = -(logp.exp() * logp).sum(dim=-1).mean()
    check(bool(torch.isclose(ent, manual).item()), "cat_entropy 与手算一致")


def test_ppo_save_load(tmp_path: Path) -> None:
    import torch.nn as nn

    model = nn.Linear(4, 2)
    opt = torch.optim.Adam(model.parameters(), lr=0.1)
    td = tempfile.mkdtemp(dir=str(tmp_path))
    ppo_common._ppo_save(td, model, opt, 3)
    # 文件齐备
    for f in ("model.pt", "opt.pt", "state.json"):
        check(os.path.exists(os.path.join(td, f)), f"checkpoint 文件 {f} 存在")
    # 加载返回 epochs_done
    m2 = nn.Linear(4, 2)
    o2 = torch.optim.Adam(m2.parameters(), lr=0.1)
    done = ppo_common._ppo_load(td, m2, o2)
    check(done == 3, f"_ppo_load 返回 epochs_done=3 (got {done})")
    check(
        np.array_equal(m2.weight.detach().numpy(), model.weight.detach().numpy()),
        "checkpoint 加载后权重一致",
    )
    # 无 checkpoint 路径
    m3 = nn.Linear(4, 2)
    o3 = torch.optim.Adam(m3.parameters(), lr=0.1)
    check(ppo_common._ppo_load(None, m3, o3) == 0, "ckpt_path=None → 0")


def main() -> None:
    # 手动运行入口：临时目录放项目内 tmp/（与 pytest basetemp 同区，避免碰系统 %TEMP% 触发沙箱权限）
    _td = tempfile.mkdtemp(dir=str(Path(__file__).resolve().parent.parent / 'tmp' / 'manual-tests'))
    test_masked_logsoftmax()
    test_cat_logprob_entropy()
    test_backend_params_match_config()
    test_ppo_save_load(Path(_td))
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()


def test_backend_params_match_config() -> None:
    """P1-1 守护：三后端本地超参与 ppo/config.py BACKEND_PARAMS 一致（调参必须同步）。"""
    from ppo.config import assert_backend_constants

    assert_backend_constants()


def test_engine_and_common_still_re_export_the_np_core_names() -> None:
    """搬家的代价守卫：搬进 np_core 后，旧访问点（engine / common）必须仍然可达。

    2026-09-26 实测踩坑（本守卫的由来）：trajectory 装载块搬去 `ppo/np_core` 后，
    engine 里那四个**只被属性取用**的再导出（`compute_gae` / `discover_shards` /
    `load_episodes_common` / `load_shard_fields`）被 ruff F401 判成死代码删掉 ⇒
    `tests/test_ppo_goal.py`（`import ppo.engine as ppo; ppo.compute_gae(...)` 当定长
    参照）当场 AttributeError。**「没人 `import` 这个名」≠「没人在属性上取它」**。
    """
    import ppo
    import ppo.engine as engine
    import ppo.np_core as np_core

    # 两条再导出链各自的公开面：common 从最早那一刀起就有的四个，engine 还多带
    # trajectory 装载那一组（2026-09-26 搬来，旧访问点 `ppo.engine.load_episodes` 等要活）。
    BOTH = ("compute_gae", "discover_shards", "load_episodes_common", "load_shard_fields")
    ENGINE_ONLY = (
        "discover_rl_shards",
        "load_episode_from_shard",
        "load_episodes",
        "load_shard",
        "GAMMA",
        "LAM",
        "_RL_SHARD_SPEC",
    )
    for name in BOTH + ENGINE_ONLY:
        assert getattr(engine, name) is getattr(np_core, name), (
            f"ppo.engine.{name} 不再是 np_core 的同一个对象（再导出断了/被抄了一份）"
        )
    for name in BOTH:
        assert getattr(ppo_common, name) is getattr(np_core, name), (
            f"ppo.common.{name} 不再是 np_core 的同一个对象（再导出断了/被抄了一份）"
        )
    # 根级便捷名也指向新家（`ppo.compute_gae` 不得再拖 torch）。
    assert ppo.compute_gae is np_core.compute_gae
    assert ppo.load_episodes is np_core.load_episodes


def _kick_chunks() -> list[dict]:
    """§363 kickstart 数值夹具：B=8 合成 minibatch（numpy 口径，与 tensored 一致）。"""
    rng = np.random.RandomState(3)
    n = 8
    return [
        {
            "obs": rng.randint(0, 255, (n, OBS_CHANNELS, 26, 26)).astype(np.uint8),
            "scalars": (rng.randn(n, SCALAR_DIM)).astype(np.float32),
            "a_move": rng.randint(0, 5, n).astype(np.int64),
            "a_fire": rng.randint(0, 2, n).astype(np.int64),
            "lp_move": (rng.randn(n) * 0.2).astype(np.float32),
            "lp_fire": (rng.randn(n) * 0.2).astype(np.float32),
            "adv": (rng.randn(n)).astype(np.float32),
            "ret": (rng.randn(n) * 2 + 1).astype(np.float32),
            "mask": np.ones((n, 7), dtype=np.int64),
        }
    ]


def _kick_models():
    import torch

    from models.student import PPOStudent

    torch.manual_seed(0)
    m = PPOStudent()
    torch.manual_seed(0)
    m2 = PPOStudent()
    torch.manual_seed(1)
    ref = PPOStudent()
    ref.eval()
    for p in ref.parameters():
        p.requires_grad = False
    return torch, m, m2, ref


def _kick_state(m) -> list:
    return [p.detach().cpu().clone() for p in m.parameters()]


def test_ppo_update_kickstart_off_is_identity() -> None:
    """缺省路径恒等：ref=None（无论 kl 为何值）与 kl=0（无论 ref 有无）跑出逐字节
    相同的权重——旧行为回归锚。"""
    import ppo.engine as engine

    torch, m1, m2, ref = _kick_models()
    chunks = _kick_chunks()
    o1 = torch.optim.Adam(m1.parameters(), lr=1e-4)
    o2 = torch.optim.Adam(m2.parameters(), lr=1e-4)
    np.random.seed(7)
    a1 = engine.ppo_update(m1, o1, chunks, 1, torch.device("cpu"))
    np.random.seed(7)
    a2 = engine.ppo_update(
        m2, o2, chunks, 1, torch.device("cpu"), ref_model=ref, kickstart_kl=0.0
    )
    assert a1["kickstart"] == 0.0 and a2["kickstart"] == 0.0
    for p, q in zip(_kick_state(m1), _kick_state(m2), strict=True):
        assert torch.equal(p, q), "关闭路径权重必须逐字节一致"


def test_ppo_update_kickstart_bites_when_armed() -> None:
    """武装路径：ref 就绪＋kl>0 → kickstart 列 >0 且权重偏离关闭路径。"""
    import ppo.engine as engine

    torch, m1, m2, ref = _kick_models()
    chunks = _kick_chunks()
    o1 = torch.optim.Adam(m1.parameters(), lr=1e-4)
    o2 = torch.optim.Adam(m2.parameters(), lr=1e-4)
    np.random.seed(7)
    engine.ppo_update(m1, o1, chunks, 1, torch.device("cpu"))
    np.random.seed(7)
    agg = engine.ppo_update(
        m2, o2, chunks, 1, torch.device("cpu"), ref_model=ref, kickstart_kl=1.0
    )
    assert agg["kickstart"] > 0, "不同初始化的 ref 应有正 KL"
    assert any(
        not torch.equal(p, q) for p, q in zip(_kick_state(m1), _kick_state(m2), strict=True)
    ), "缰绳应改变更新轨迹"


def test_demo_index_reuses_the_same_tensor_and_keeps_values() -> None:
    """demo 混 batch 的索引必须走**复用的设备张量**（否则 XLA 每步重编译）。

    真机定案（2026-09-22）：把 host numpy 索引直接交给高级索引 ⇒ 同一批 B/flags 下连续两步
    各 `新=2`（新编译）；复用同一设备缓冲 / 先 mark 物化 ⇒ `新=0`。离线课程 8~10s/步就是
    这个（单步 2 次新编译 ≈11s；编译命中的那一步 0.31s）。
    """
    buf = torch.zeros(3, dtype=torch.int64)
    a = ppo_common.demo_index(buf, np.array([2, 0, 1], dtype=np.int64))
    assert a is buf, "返回的必须是那张缓冲本身（图签名靠它恒定）"
    assert a.tolist() == [2, 0, 1]
    b = ppo_common.demo_index(buf, np.array([1, 1, 0], dtype=np.int64))
    assert b is buf, "第二次调用也不得新建张量"
    assert b.tolist() == [1, 1, 0]


def test_demo_index_selection_matches_plain_numpy_indexing() -> None:
    """数值逐位相同：换索引来路不得抽到别的样本。"""
    bank = torch.arange(15, dtype=torch.float32).reshape(5, 3)
    buf = torch.zeros(4, dtype=torch.int64)
    for _ in range(5):
        idx = np.random.randint(0, 5, size=4).astype(np.int64)
        assert torch.equal(bank[ppo_common.demo_index(buf, idx)], bank[idx])


def test_demo_index_none_buffer_returns_the_numpy_index() -> None:
    """非 demo 路径（buf=None）行为与接线前逐字节一致。"""
    idx = np.array([3, 1], dtype=np.int64)
    assert ppo_common.demo_index(None, idx) is idx

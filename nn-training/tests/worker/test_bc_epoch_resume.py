"""BC `on_epoch` 回传 + 中断接续（2026-09-13）——真训练的接续编号语义。

对应需求：bc 任务云端**每个 epoch 完成**都回传权重（hub 单文件 resume + 指标 jsonl），
中断重启从最后完成的 epoch 接续（--resume + epoch_offset），绝不从头重训。

**本文件需要真 torch**（`bc_train` 真跑 2 epoch）。纯存储/课程那 6 条 2026-09-26 分家：
  * hub 存储语义（store_bc_epoch / 租约门 / 404 语义）→ `test_bc_resume_store.py`（免 torch）；
  * 课程 `eval` 块解析 → `test_bc_course.py`（免 torch）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from common.schema import OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.server import _JobStore
from worker.train.bc import SMOOTH_WINDOW, select_best_epoch, smoothed_val_loss
from worker.train.bc import train as bc_train

# ------------------------------------------------------------------ hub 存储


# ------------------------------------------------------------------ bc.on_epoch（接续训练钩子）


def _make_corpus(tmp_path: Path, n: int = 16) -> Path:
    """最小语料：钩子语义只需要 on_epoch 被按全局 epoch 调用，不测收敛。

    n=16（原 60）：Windows + xdist 下 2 epoch 真 torch 训练曾 5.5–6.2s 超 5s 预算
    （同机 WSL <5s）；样本减到 1 个 batch 后 call 阶段回到 ~1–2s。语义断言不变。
    """
    rng = np.random.default_rng(7)
    d = tmp_path / "shard0"
    d.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, 26, 26), dtype=np.uint8),
        "scalars": rng.standard_normal((n, SCALAR_DIM)).astype(np.float32),
        # v5（plan/obs-v5-vector-gap.plan.md）：POLICY_EXTRA(37) 随样本走。
        "extra": rng.random((n, POLICY_EXTRA_DIM)).astype(np.float32),
        # move ∈ 0..4；fire ∈ 0..1（FIRE_DIM=2——随机 0..4 会让 masked CE 目标越界）
        "actions": np.stack(
            [rng.integers(0, 5, n), rng.integers(0, 2, n)], axis=1
        ).astype(np.int64),
        "masks": np.ones((n, 7), dtype=np.float32),
        "conditions": np.zeros(n, dtype=np.int64),
        "returns": rng.standard_normal(n).astype(np.float32),
    }
    from worker.data.npyio import save_shard

    save_shard(str(d), arrays, {"stage": 0, "seed": 1})
    return tmp_path


def _bc_args(tmp_path: Path, **over: object) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        data_dir=str(_make_corpus(tmp_path)),
        arch="student",
        out=str(tmp_path / "bc-weights.json"),
        notes="test",
        resume=None,
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=2,
        batch=8,
        lr=1e-3,
        val_split=0.2,
        mirror_p=0.0,
        seed=1234,
        num_workers=0,
        device="cpu",
        value_coef=0.0,
        on_epoch=None,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_bc_train_on_epoch_called_per_epoch(tmp_path: Path) -> None:
    calls: list[tuple[int, dict]] = []
    ns = _bc_args(
        tmp_path, on_epoch=lambda gepoch, raw, m: calls.append((gepoch, dict(m)))
    )
    bc_train(ns)
    assert [g for g, _ in calls] == [1, 2]  # 全局 epoch 编号
    assert set(calls[0][1]) == {"train_loss", "val_loss", "move_acc", "fire_acc", "lr"}


def test_bc_train_reports_the_epoch_it_actually_delivers(tmp_path: Path) -> None:
    """B1：窗口未满（2 < 5）时交最后一轮，`best_val` 是该轮的原始 val_loss。"""
    out = bc_train(_bc_args(tmp_path))
    assert out["delivered_epoch"] == 2
    # history 按 4 位小数落账，返回值是未舍入的原始 float：容差取落账精度。
    assert out["best_val_loss"] == pytest.approx(out["history"]["val_loss"][-1], abs=1e-4)
    assert out["best_smoothed_val_loss"] == pytest.approx(
        smoothed_val_loss(out["history"]["val_loss"], window=SMOOTH_WINDOW)
    )


def test_bc_train_delivers_epoch_chosen_by_smoothed_val_loss(tmp_path: Path) -> None:
    """B1：epochs >= 窗口时，交付 epoch = 完整窗口滑动平均的最小值（不看单点）。"""
    out = bc_train(_bc_args(tmp_path, epochs=6))
    delivered = select_best_epoch(out["history"]["val_loss"], window=SMOOTH_WINDOW)
    assert delivered is not None and delivered >= SMOOTH_WINDOW
    assert out["delivered_epoch"] == delivered
    assert out["best_val_loss"] == pytest.approx(
        out["history"]["val_loss"][delivered - 1], abs=1e-4
    )
    meta = json.loads((tmp_path / "bc-weights.json").read_text(encoding="utf-8"))
    assert meta["delivered_epoch"] == delivered
    # 落账口径对落账口径（2026-11-15，双生同步）：meta 是 `round(best_smoothed, 4)`，返回值是原始
    # float。`pytest.approx(raw)` 的默认相对容差 1e-6 挡不住 4dp 舍入（最大 5e-5）⇒ 值 ≳5 时
    # 会偶发红（paddle 双生那条就是这么红的）。两边取 4dp = 精确相等。
    assert meta["best_smoothed_val_loss"] == round(out["best_smoothed_val_loss"], 4)


def test_ledger_value_must_be_compared_at_ledger_precision() -> None:
    """2026-11-15 门禁偶发红的根因存档：4dp 落账值不能拿**默认相对容差**跟原始 float 对账。

    现场值：`best_smoothed_val_loss` 原始 `25.586440000000003`、落账 `25.5864`，差 **4e-5**；
    `pytest.approx(raw)` 的默认 `rel=1e-6` 在 25 量级只给 2.6e-5 余量 ⇒ 判红。触发条件是
    「原始值的尾巴恰好落在 4dp 网格外」，所以它**偶发**：同一份语料换个 epoch 曲线就绿了。
    上面几条对账因此都改成「两边都取 4dp」——精确相等，不需要容差，也不会再偶发。
    """
    raw = 25.586440000000003
    assert raw != round(raw, 4), "前提：这个现场值确实不在 4dp 网格上"
    assert round(raw, 4) != pytest.approx(raw), "默认相对容差挡不住 4dp 舍入（这就是那个 flake）"
    assert round(raw, 4) == round(raw, 4)


def test_bc_train_resume_continues_epoch_numbering(tmp_path: Path) -> None:
    """中断接续语义：resume + epoch_offset=k → 下一 epoch 编号 k+1（ckpt/归档/回传
    编号连续），不是从头重训。"""
    first = _bc_args(tmp_path)
    bc_train(first)  # 2 epoch（全局 1..2）
    calls: list[int] = []
    ns = _bc_args(
        tmp_path,
        resume=str(tmp_path / "bc-weights.json"),
        epoch_offset=2,
        epochs=2,
        out=str(tmp_path / "bc-weights2.json"),
        on_epoch=lambda gepoch, raw, m: calls.append(gepoch),
    )
    bc_train(ns)
    assert calls == [3, 4]  # 接续编号，不撞第一段


# ------------------------------------------------------------------ 课程 eval 块




def test_bc_train_writes_null_when_val_loss_is_not_finite(tmp_path: Path) -> None:
    """验证集的 val_loss 算不出（NaN）⇒ 账本写 `null`，绝不写 `NaN`/`Infinity` 字面量。

    为什么这条重要：`weights.json` 是 TS 运行时与 dashboard 都读的落盘面，而 `json.dump` 会把
    非有限 float 写成**非标准**的 `NaN` / `Infinity` —— `JSON.parse` 直接抛错，坏的是「打开就崩」
    而不是「数值不准」。这一轮修的是选轮（`select_best_epoch` 在无有限窗口时返回 None），
    落盘面必须跟着给出可读的 null。

    构造方式用**真实切分**：先跑一次 `make_loaders` 拿到 val 下标（同一个 seed ⇒ 逐字节可复现），
    只把那些行的 scalars 写成 NaN —— 训练行的梯度仍是干净的，所以权重有限、文件照写。
    """
    from worker.data.dataset import make_loaders

    # ⚠ `_bc_args` 自己会调 `_make_corpus`（覆盖写）；先把 args 建好，再改盘上的 shard。
    ns = _bc_args(tmp_path, epochs=2)
    corpus = Path(ns.data_dir)
    _, val_dl, _ = make_loaders(
        str(corpus), batch_size=8, val_split=0.2, mirror_p=0.0, seed=1234
    )
    val_idx = sorted(int(i) for i in val_dl.dataset.indices)
    assert val_idx, "val_split=0.2 必须有验证样本，否则这条用例什么都没测"
    shard = corpus / "shard0" / "scalars.npy"
    arr = np.load(shard)
    arr[val_idx, :] = np.nan
    np.save(shard, arr)

    out = bc_train(ns)
    assert out["best_smoothed_val_loss"] != out["best_smoothed_val_loss"]  # NaN，正是现场

    artifact = tmp_path / "bc-weights.json"
    raw = artifact.read_text(encoding="utf-8")

    def _reject_const(literal: str) -> None:
        raise AssertionError(
            f"weights.json 里出现了非标准 JSON 字面量 {literal}（TS 端 JSON.parse 会直接抛错）"
        )

    # 用 `parse_constant` 判，不 grep 文本：权重是 base64，随便就会撞出 "NaN" 子串。
    meta = json.loads(raw, parse_constant=_reject_const)
    assert meta["best_smoothed_val_loss"] is None
    assert meta["best_val_loss"] is None

"""BC 每 epoch 回传 + 中断接续（2026-09-13）——hub 存储 / bc.on_epoch / eval 块测试。

对应需求：bc 任务云端**每个 epoch 完成**都回传权重（hub 单文件 resume + 指标 jsonl），
中断重启从最后完成的 epoch 接续（--resume + epoch_offset），绝不从头重训；
课程 `eval` 块多地图配置解析。

hub 侧直接测 `_JobStore`（磁盘存储 + 租约门，与 test_hub_leases 同 harness；
时间 fake 时钟注入，不碰 HTTP 层）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from schema import OBS_CHANNELS, SCALAR_DIM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _JobStore
from train.bc import train as bc_train


def _clock(t: float = 1000.0):
    class _C:
        def __init__(self) -> None:
            self.t = t

        def __call__(self) -> float:
            self.t += 0.001
            return self.t

    return _C()


def _mini_store(tmp_path: Path) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=_clock())


def _epoch_body(epoch: int) -> dict:
    return {
        "epoch": epoch,
        "weights": "H4sIAAAAAAAA//KtKs7PLUlN0clMLQZxQUJOBgDlGt0eCwAAAA==",  # gzip(b'{}')
        "metrics": {
            "train_loss": 1.5,
            "val_loss": 1.4,
            "move_acc": 0.5,
            "fire_acc": 0.7,
            "lr": 3e-3,
        },
    }


# ------------------------------------------------------------------ hub 存储


def test_store_bc_epoch_roundtrip_and_overwrite(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(1)) is True
    first = store.get_bc_resume(jid)
    assert first is not None and first["epoch"] == 1
    # 覆盖语义：最新 epoch 胜（磁盘有界：恒 1 份 resume）
    assert store.store_bc_epoch(jid, _epoch_body(7)) is True
    assert store.get_bc_resume(jid)["epoch"] == 7  # type: ignore[index]
    # 指标行追加（不覆盖）
    rows = store.get_bc_metrics(jid)
    assert [r["epoch"] for r in rows] == [1, 7]
    assert rows[0]["move_acc"] == 0.5


def test_store_bc_epoch_invalid_body_rejected(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, {"epoch": 0}) is False  # epoch < 1
    assert store.store_bc_epoch(jid, {"epoch": 3}) is False  # 缺 weights
    assert store.store_bc_epoch(jid, {"epoch": True, "weights": "x"}) is False  # bool 冒充 int
    assert store.get_bc_resume(jid) is None and store.get_bc_metrics(jid) == []


def test_bc_epoch_lease_gate(tmp_path: Path) -> None:
    """活租约期非持有人不得写 resume（防被顶掉的旧 worker 用旧 epoch 覆盖新 resume）；
    无租约（过期/重启后）照收——resume 是幂等覆盖存最新。"""
    store = _mini_store(tmp_path)
    jid = "j" * 16
    jd = tmp_path / "jobs" / jid
    jd.mkdir(parents=True)
    (jd / "manifest.json").write_text("{}", encoding="utf-8")
    tok = store.claim(jid, ttl=300)
    assert tok is not None
    assert store.result_token_ok(jid, "wrong-token") is False
    assert store.result_token_ok(jid, tok) is True
    store.release(jid, tok)
    assert store.result_token_ok(jid, "wrong-token") is True  # 无租约照收


def test_bc_resume_missing_is_404_semantic(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.get_bc_resume(jid) is None  # worker GET 404 → 全新训练


# ------------------------------------------------------------------ bc.on_epoch（接续训练钩子）


def _make_corpus(tmp_path: Path, n: int = 60) -> Path:
    rng = np.random.default_rng(7)
    d = tmp_path / "shard0"
    d.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, 26, 26), dtype=np.uint8),
        "scalars": rng.standard_normal((n, SCALAR_DIM)).astype(np.float32),
        # move ∈ 0..4；fire ∈ 0..1（FIRE_DIM=2——随机 0..4 会让 masked CE 目标越界）
        "actions": np.stack(
            [rng.integers(0, 5, n), rng.integers(0, 2, n)], axis=1
        ).astype(np.int64),
        "masks": np.ones((n, 7), dtype=np.float32),
        "conditions": np.zeros(n, dtype=np.int64),
        "returns": rng.standard_normal(n).astype(np.float32),
    }
    from data.npyio import save_shard

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
        batch=32,
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


def test_bc_course_eval_block_multi_level() -> None:
    from rl.bc_config import load_bc_course

    c = load_bc_course("bc-c4")
    assert c.eval.enabled is True
    assert c.eval.every_epochs == 10
    assert c.eval.games_per_stage == 10
    assert c.eval.levels == ["arena4", "arena6"]  # 多地图


def test_bc_course_eval_block_default_off() -> None:
    from rl.bc_config import load_bc_course

    assert load_bc_course("bc-e2e").eval.enabled is False  # 夹具不配 eval

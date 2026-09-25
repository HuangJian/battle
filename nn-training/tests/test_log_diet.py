"""tests/test_log_diet.py —— 云端日志节食的**接线**（2026-09-24）。

原语契约在 `tests/test_log_bundle.py`；这里钉的是「哪些行真的被攒进了那一行」。
为什么要自动化钉：这条改动的收益是**行数**（一个跑一次算不出来的量），而它正是把控制台
日志面板拖死的那个变量——哪天有人为了调试把它改回逐行打印，本文件必须先红。四处接线：

  ① `ppo/common.load_episodes_common(bundle=…)`：装载四行 → 一行；不传 bundle 时逐字不变；
  ② `ppo/engine.ppo_update(progress=…)`：epoch 行 + 收尾行 → 一行；不传时逐字不变；
  ③ `remote/iter_rollout.run_iter_rollout`：轮设置/看门狗/池/进度/收尾/单局耗时 → 一行，
     **中断时也要打**（否则「为什么被杀了」无从归因）；
  ④ `remote/worker` 侧的两个攒行出口（`prune_job_dirs` / `_ensure_ts_code`）。

断言一律看**可观察行为**（打了什么行、哪一行里有什么），不看源码行号。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import ppo.common as C
from log_bundle import LogBundle


def _rig() -> tuple[LogBundle, list[str]]:
    """bundle + 它打出来的行（同一份 append）。"""
    lines: list[str] = []
    return LogBundle(lines.append), lines


# --------------------------------------------------------------- ① 装载四行 → 一行


def _fake_shard(n: int, stage: int) -> dict:
    return {
        "obs": np.zeros((n, 2), dtype=np.uint8),
        "mask": np.ones((n, 2), dtype=np.uint8),
        "value": np.zeros(n, dtype=np.float32),
        "reward": np.zeros(n, dtype=np.float32),
        "done": np.zeros(n, dtype=np.float32),
        "stage": np.asarray(stage),
    }


def _load(monkeypatch: pytest.MonkeyPatch, shards: list[tuple[int, int]], *, quota: int,
          bundle: LogBundle | None, logs: list[str]) -> list[dict]:
    """用假 shard 表驱动 `load_episodes_common`（与 `test_ppo_quota._collect` 同口径）。"""
    monkeypatch.setattr(C, "discover_shards", lambda root, need: list(range(len(shards))))
    monkeypatch.setattr(C, "log", logs.append)

    def loader(sd):  # 无注解 = 与 `test_ppo_quota._collect` 同口径（mypy 视作 Callable[..., Any]）
        n, stage = shards[sd]
        return _fake_shard(n, stage)

    return C.load_episodes_common(
        "R",
        label="ppo",
        shard_kind="RL",
        need_files=("metrics.npy", "obs.npy"),
        shard_loader=loader,
        gae=lambda d: (np.zeros(d["obs"].shape[0], dtype=np.float32),
                       np.zeros(d["obs"].shape[0], dtype=np.float32)),
        gae_name="GAE",
        normalize_ret=False,
        per_stage_quota=quota,
        bundle=bundle,
    )


def test_load_folds_four_lines_into_the_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """给了 bundle ⇒ 一行都不 log；四件事实（shards/装载/episodes/配额）都在 parts 里。"""
    b, lines = _rig()
    logs: list[str] = []
    eps = _load(monkeypatch, [(8, 0), (8, 0)], quota=0, bundle=b, logs=logs)
    assert len(eps) == 2
    assert logs == [], "给了 bundle 就不该再逐行打（节食的全部意义）"
    text = b.text()
    for part in ("shards=2 RL ← R", "装载=2/2", "episodes=2 eps（GAE 已算）"):
        assert part in text, text
    assert "配额" not in text, "无配额路线不该凭空多一栏"


def test_load_keeps_the_shortage_warning_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """供给不足（SHORT）是**要看的**：攒行不许把它埋掉 ⇒ 单独一句 note。"""
    b, lines = _rig()
    logs: list[str] = []
    _load(monkeypatch, [(4, 0)], quota=100, bundle=b, logs=logs)
    assert "per-stage quota=100: kept 4 steps" in b.text()
    assert "SHORT (供给不足) stages={0: 96}" in b.text(), b.text()


def test_load_without_bundle_is_byte_for_byte_the_old_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回退路径（goal/intent/本机三条线共用）：不传 bundle 时四行照旧。"""
    logs: list[str] = []
    _load(monkeypatch, [(8, 0)], quota=0, bundle=None, logs=logs)
    joined = "\n".join(logs)
    assert "[ppo] loaded 1 RL shards from R" in joined
    assert "[ppo] shard IO + GAE done for 1 episodes" in joined


# ------------------------------------------------------- ② epoch 行 + 收尾 → 一行


def _engine_rig(monkeypatch: pytest.MonkeyPatch):
    import ppo.engine as engine
    from tests.test_ppo_common import _kick_chunks, _kick_models

    torch, model, _m2, _ref = _kick_models()
    opt = torch.optim.Adam(model.parameters(), lr=1e-4)
    msgs: list[str] = []
    monkeypatch.setattr(engine, "log", msgs.append)
    return engine, torch, model, opt, _kick_chunks(), msgs


def test_engine_folds_epochs_into_the_progress_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """epoch 行不再单独打；两个 epoch 只留**最后一个**读数（同一行、位置不变）。"""
    engine, torch, model, opt, chunks, msgs = _engine_rig(monkeypatch)
    b = LogBundle(msgs.append)
    np.random.seed(7)
    engine.ppo_update(
        model,
        opt,
        chunks,
        2,
        torch.device("cpu"),
        progress=b,
        progress_head="job j1: PPO 训练中",
    )
    assert not any("epoch 1/2 done" in m for m in msgs), msgs
    assert not any("[ppo] epoch" in m for m in msgs), msgs
    assert b.pending() is True, "没 emit 之前内容必须还攒着（收尾由调用方补 steps/chunks）"
    body = b.text()
    assert "epoch 2/2 done" in body and "epoch 1/2 done" not in body, body
    assert "kl=" in body and "gnorm=" in body, body
    # 收尾：调用方 emit 一行（这就是 PPO 那一行）
    assert b.emit("job j1: PPO done in 12.3s") is True
    assert msgs[-1].startswith("job j1: PPO done in 12.3s") and "epoch 2/2 done" in msgs[-1]


def test_engine_without_bundle_still_logs_per_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退路径（CLI / 旧调用方）：不传 progress 时逐 epoch 一行，逐字不变。"""
    engine, torch, model, opt, chunks, msgs = _engine_rig(monkeypatch)
    np.random.seed(7)
    engine.ppo_update(model, opt, chunks, 1, torch.device("cpu"))
    assert any(m.startswith("[ppo] epoch 1/1 done") for m in msgs), msgs


def test_engine_diag_is_one_line_not_one_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """XLA 诊断：逐窗口只累计 ⇒ CPU 上（无 diag）也不该出现逐窗口行；源码面由
    `tests/test_xla_step_diag.py::test_diag_is_one_line_at_the_end` 钉住。"""
    engine, torch, model, opt, chunks, msgs = _engine_rig(monkeypatch)
    np.random.seed(7)
    engine.ppo_update(model, opt, chunks, 1, torch.device("cpu"))
    assert not any("diag s=" in m for m in msgs), msgs


# ------------------------------------------------- ③ rollout 一轮 → 一行（含中断）


def test_iter_round_is_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功一轮：**一行**收尾，且设置/进度/单局耗时都在那一行里。"""
    from remote.iter_rollout import run_iter_rollout
    from tests.test_remote_iter import _STUB_FLAKY, _fast_watchdog, _one_game_spec

    _fast_watchdog(monkeypatch)
    marker = tmp_path / "flaky.marker"
    script = tmp_path / "flaky.py"
    script.write_text(_STUB_FLAKY.replace("@MARKER@", str(marker)), encoding="utf-8")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    run_iter_rollout(job_dir, _one_game_spec(tmp_path, script), log=msgs.append)

    done = [m for m in msgs if m.startswith("kind=iter rollout done")]
    assert len(done) == 1, msgs
    line = done[0]
    for part in ("games=1", "workers=1", "bun=", "ts_root=", "进度=1/1 games settled",
                 "winRate=", "单局耗时"):
        assert part in line, (part, line)
    # 逐行刷屏的那几族不许再出现
    assert not any(m.startswith("kind=iter rollout: ") for m in msgs), msgs
    assert not any(m.startswith("kind=iter 单局看门狗") for m in msgs), msgs
    assert not any(m.startswith("kind=iter 长驻 worker 池") for m in msgs), msgs


def test_iter_abort_still_prints_the_watchdog_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中断（整轮失败）也必须交代现场：看门狗口径 + 已结算到哪一局在同一行。"""
    from common.protocol import RetryableError
    from remote.iter_rollout import run_iter_rollout
    from tests.test_remote_iter import _STUB_HANG, _fast_watchdog, _one_game_spec

    _fast_watchdog(monkeypatch)
    script = tmp_path / "hang.py"
    script.write_text(_STUB_HANG, encoding="utf-8")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    with pytest.raises(RetryableError):
        run_iter_rollout(
            job_dir, _one_game_spec(tmp_path, script, game_timeout_sec=0.2), log=msgs.append
        )
    aborted = [m for m in msgs if "中断" in m]
    assert len(aborted) == 1, msgs
    assert "看门狗" in aborted[0] and "games=" in aborted[0], aborted[0]


# ------------------------------------------------------ ④ worker 侧两个攒行出口


def test_prune_reports_into_the_bundle(tmp_path: Path) -> None:
    from remote.worker import prune_job_dirs

    for i in range(3):
        (tmp_path / f"j{i}").mkdir()
    b, lines = _rig()
    logs: list[str] = []
    removed = prune_job_dirs(tmp_path, keep=1, log=logs.append, bundle=b)
    assert removed == 2
    assert logs == [], "给了 bundle 就不该再单独打一行"
    assert "prune=删 2 个旧 job 目录（保留最近 1 个）" in b.text(), b.text()


def test_ts_code_cache_hit_reports_into_the_bundle(tmp_path: Path) -> None:
    """重复的第二轮（同 sha 命中）在 bundle 里留痕，不再单独一行。"""
    import hashlib

    from remote.worker import _ensure_ts_code
    from tests.test_remote_iter import _ts_zip

    raw = _ts_zip({"tools/sim/export-rl-rollout.ts": "// x"})
    manifest = {"ts_code_sha256": hashlib.sha256(raw).hexdigest()}
    ts_root = tmp_path / "ts_code_cache"
    _ensure_ts_code(
        "http://unused", "tok", "j1", manifest,
        ts_root=ts_root, preloaded={"ts_code_zip": raw}, log=lambda _m: None,
    )
    b, lines = _rig()
    logs: list[str] = []
    _cache, n, hit = _ensure_ts_code(
        "http://unused", "tok", "j2", manifest,
        ts_root=ts_root, preloaded=None, log=logs.append, bundle=b,
    )
    assert hit is True and n == 0
    assert logs == [], "命中那行也该进 bundle"
    assert "ts_code=cache 命中" in b.text(), b.text()

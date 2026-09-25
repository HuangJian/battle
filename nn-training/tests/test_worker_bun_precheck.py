"""worker 的「缺 bun ⇒ 零下载拒单」能力自检（plan/train-mode-hot-switch.plan.md §3 / L3.2）。

守的是**设计决定**，不是实现细节：

  ① `kind=iter` ∧ 节点没 bun ⇒ 抛 `ProtocolError` **且一个字节都不下**（`kind=run`
     在更前面就被拒了：那条腿已退役，见 `tests/test_offline_leg_retired.py`）
     （payload / code.zip / ts_code.zip 三件都在自检之后才取；户报障现场是真机
     3.42MB / 12.1s 全白传之后才发现没 bun）；
  ② `echo=True` 豁免：echo 走「只验传输链」，整个跳过 rollout（`worker.py` 的 kind 分叉），
     不该要求 bun；
  ③ `kind=ppo` 豁免：只有「节点自己跑 rollout」才需要 bun（F4）；
  ④ **位置判据**：结果复用块（`_result.json`）在自检**之前** —— 已算完、只差重传的 job
     不该因为节点上 bun 没了而被拒（纯重传不需要 bun）。

判据都是可观察行为（下没下、抛什么），不看源码行号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remote import worker
from remote.protocol import ProtocolError, normalize_manifest

JID = "j" * 16


class _DownloadedError(Exception):
    """下载哨兵：**证明**流程走到了取 payload 那一步（= 没被自检拦下）。"""


def _manifest(kind: str) -> dict:
    """最小合法 manifest（`tests/test_run_loop.py::_manifest` 同形状；按 kind 增删可选键）。"""
    raw: dict = {
        "proto": 1,
        "kind": kind,
        "runId": "run-buncheck",
        "it": 1,
        "job_id": JID,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 8,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }
    if kind in ("iter", "run"):  # kind=run 的 manifest 由 test_offline_leg_retired 复用
        raw["ts_code_sha256"] = "t" * 64
        raw["rollout"] = {
            # 一局的完整命令（argv 校验：白名单脚本 + --stages/--seeds 各一次 + --out/--weights 相对路径）
            "argv": [
                [
                    "tools/sim/export-rl-rollout.ts",
                    "--out",
                    "w0",
                    "--weights",
                    "w0/weights.json",
                    "--stages",
                    "3",
                    "--seeds",
                    "11",
                ]
            ],
            "wver": "w" * 64,
            "workers": 1,
            "game_timeout_sec": 0.0,
            "bun": "bun",
        }
    if kind == "run":
        raw["plan_sha256"] = "q" * 64
    return normalize_manifest(raw)


def _job(kind: str) -> dict:
    return {"job_id": JID, "manifest": _manifest(kind)}


@pytest.fixture()
def no_bun(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点上没有 bun（`resolve_bun` 的 `shutil.which` 查不到）。"""
    monkeypatch.setattr("shutil.which", lambda _name: None)


@pytest.fixture()
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把下载拦成哨兵：返回「被调用过的 jid 列表」，调用即抛 `_DownloadedError`。"""
    seen: list[str] = []

    def _spy(_base: str, _token: str, jid: str, **_kw: object) -> bytes:
        seen.append(jid)
        raise _DownloadedError(f"不该下载：{jid}")

    monkeypatch.setattr(worker, "download_payload", _spy)
    return seen


@pytest.mark.parametrize("kind", ["iter"])
def test_missing_bun_rejects_before_any_download(
    tmp_path: Path, no_bun: None, downloads: list[str], kind: str
) -> None:
    """① 没 bun ⇒ ProtocolError（确定性拒绝链的那条）+ 零字节下载。"""
    with pytest.raises(ProtocolError, match="找不到"):
        worker.run_job("http://hub", "tok", _job(kind), work_dir=tmp_path)
    assert downloads == []


def test_echo_is_exempt_from_the_bun_check(
    tmp_path: Path, no_bun: None, downloads: list[str]
) -> None:
    """② echo 只验传输链（不跑 rollout）⇒ 没 bun 也照常往下走。"""
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("iter"), work_dir=tmp_path, echo=True)
    assert downloads == [JID]


def test_ppo_is_exempt_from_the_bun_check(
    tmp_path: Path, no_bun: None, downloads: list[str]
) -> None:
    """③ kind=ppo（本机采样 + 云机只算 PPO）不需要 bun。"""
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("ppo"), work_dir=tmp_path)
    assert downloads == [JID]


def test_bun_present_does_not_block_the_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, downloads: list[str]
) -> None:
    """正向对照：bun 在 ⇒ 自检放行（不是拦路虎）。"""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/bun")
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("iter"), work_dir=tmp_path)
    assert downloads == [JID]


def test_cached_result_is_reused_even_without_bun(
    tmp_path: Path, no_bun: None, downloads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """④ 复用块在自检之前：上次算完、只差重传的 job 不该被「缺 bun」拒掉（用 `iter` 验序）。

    `validate_result` 在这里被打桩成通过 —— 本用例钉的是**顺序**（复用优先于能力自检），
    不是结果体的字段校验（那由 `test_remote_ppo` 一族覆盖）。
    """
    monkeypatch.setattr(worker, "validate_result", lambda *_a, **_k: None)
    cached = {"job_id": JID, "kind": "iter", "echo": False}
    job_dir = tmp_path / JID
    job_dir.mkdir(parents=True)
    (job_dir / "_result.json").write_text(json.dumps(cached), encoding="utf-8")
    assert worker.run_job("http://hub", "tok", _job("iter"), work_dir=tmp_path) == cached
    assert downloads == []

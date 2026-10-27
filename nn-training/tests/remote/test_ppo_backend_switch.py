"""Paddle 后端的**节点侧接线**（2026-10-27，AI Studio 训练任务镜像只带 Paddle）。

三条口径（用户 2026-10-27）：
  ① hub 统一收发 torch 格式的 model.pt / opt.pt，Paddle worker 自己转（`common.torch_pickle`
     + `worker.ppo.ckpt_convert`，行为见 `tests/worker/test_ckpt_convert.py`）；
  ② hub **不需要知道** worker 跑在 torch 还是 paddle ⇒ `--backend` 是节点本地开关
     （**显式参数**：`worker_loop` → `run_one_round` → `run_job`；env 通道 2026-10-27
     评审删除，见 `tests/worker/test_single_ppo_path.py` ③），不进 manifest；
  ③ 两类 worker **混跑同一门课** ⇒ `resolve_ppo_backend("auto")` 在混合机上必须保持
     torch 优先（今天的行为逐字节不变），只有纯 Paddle 机才落 paddle。

本文件钉住的是「壳」这一半：
  * 后端解析（含显式值**不兜底**：要 paddle 却没装 ⇒ 在那台机器上响亮失败）；
  * Paddle worker **只收 kind=ppo** —— bc / 节点侧 rollout 仍是 torch/bun 语义（迁移范围
    = 只 PPO），且拒单必须发生在**任何下载之前**；
  * `--device auto` 在 Paddle 后端要问 **Paddle** 有没有 GPU。`normalize_ppo_device` 问的是
    torch（纯 Paddle 机上恒 False）⇒ 不问这一下，GPU 任务会被静默降成 cpu（慢两个数量级，
    日志一切正常 —— 与 2026-09-15 的 auto 事故同一形状）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.protocol import ProtocolError, normalize_manifest
from remote import bc_job as bc
from remote import download as DL
from remote import worker

JID = "j" * 16


# ───────────────────────── ① 后端解析（纯函数） ─────────────────────────


def test_auto_prefers_torch_when_both_frameworks_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """混合机的行为必须逐字节不变：两个框架都在 ⇒ torch（今天的生产路径）。"""
    monkeypatch.setattr(bc, "_module_available", lambda _name: True)
    assert bc.resolve_ppo_backend("auto") == "torch"


def test_auto_falls_back_to_paddle_on_a_paddle_only_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bc, "_module_available", lambda name: name == "paddle")
    assert bc.resolve_ppo_backend("auto") == "paddle"


def test_auto_with_no_framework_stays_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个都没有 ⇒ torch：让 `import torch` 在训练核里响亮失败，不在这里猜替代品。"""
    monkeypatch.setattr(bc, "_module_available", lambda _name: False)
    assert bc.resolve_ppo_backend("auto") == "torch"


def test_explicit_backend_is_not_second_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式值原样返回（不做可用性兜底）——想要 paddle 却装不上，就该在那台机器上报错。"""
    monkeypatch.setattr(bc, "_module_available", lambda _name: True)
    assert bc.resolve_ppo_backend("paddle") == "paddle"
    assert bc.resolve_ppo_backend(" TORCH ") == "torch"


def test_unknown_backend_is_a_config_error() -> None:
    with pytest.raises(ProtocolError, match="未知 PPO backend"):
        bc.resolve_ppo_backend("tensorflow")


def test_paddle_cuda_available_asks_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    """`is_compiled_with_cuda()` + `device_count()` —— 两条都满足才算有 GPU。"""

    def _fake(compiled: bool, count: int) -> SimpleNamespace:
        return SimpleNamespace(
            is_compiled_with_cuda=lambda: compiled,
            device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: count)),
        )

    monkeypatch.setitem(sys.modules, "paddle", _fake(True, 2))
    assert bc.paddle_cuda_available() is True
    monkeypatch.setitem(sys.modules, "paddle", _fake(True, 0))
    assert bc.paddle_cuda_available() is False
    monkeypatch.setitem(sys.modules, "paddle", _fake(False, 8))
    assert bc.paddle_cuda_available() is False


def test_paddle_cuda_available_is_false_without_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sys.modules["paddle"] = None` 让 `import paddle` 抛 ImportError（等价于没装）。"""
    monkeypatch.setitem(sys.modules, "paddle", None)
    assert bc.paddle_cuda_available() is False


# ───────────────────────── ② kind=ppo 限收 + 零下载拒单 ─────────────────────────


class _DownloadedError(Exception):
    """下载哨兵：**证明**流程走到了取 payload 那一步（= 没被后端自检拦下）。"""


def _manifest(kind: str) -> dict:
    """最小合法 manifest（与 `tests/remote/test_worker_bun_precheck.py` 同形状）。"""
    raw: dict = {
        "proto": 1,
        "kind": kind,
        "runId": "run-backend",
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
    if kind == "bc":
        raw["arch"] = "bc"  # MANIFEST_BC_EXTRA：bc 的必填（PPO 专有项被豁免）
        raw["mode"] = "bc"  # normalize_manifest 钉住 kind=bc ⇔ mode='bc'
    if kind == "iter":
        raw["ts_code_sha256"] = "t" * 64
        raw["rollout"] = {
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
    return normalize_manifest(raw)


def _job(kind: str) -> dict:
    return {"job_id": JID, "manifest": _manifest(kind)}


@pytest.fixture()
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把下载拦成哨兵：返回「被调用过的 jid 列表」，调用即抛 `_DownloadedError`。"""
    seen: list[str] = []

    def _spy(_base: str, _token: str, jid: str, **_kw: object) -> bytes:
        seen.append(jid)
        raise _DownloadedError(f"不该下载：{jid}")

    monkeypatch.setattr(DL, "download_payload", _spy)
    return seen


@pytest.mark.parametrize("kind", ["bc", "iter"])
def test_paddle_backend_rejects_non_ppo_kinds_before_any_download(
    tmp_path: Path,
    downloads: list[str],
    kind: str,
) -> None:
    """迁移范围 = 只 PPO ⇒ bc / 节点侧 rollout 在 Paddle 后端上**零字节**拒收。"""
    with pytest.raises(ProtocolError, match="只跑 kind=ppo"):
        worker.run_job("http://hub", "tok", _job(kind), work_dir=tmp_path, backend="paddle")
    assert downloads == []


def test_paddle_backend_lets_ppo_through(tmp_path: Path, downloads: list[str]) -> None:
    """正向对照：kind=ppo 照常走下载（拒收的是 kind，不是 paddle 本身）。"""
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("ppo"), work_dir=tmp_path, backend="paddle")
    assert downloads == [JID]


def test_torch_backend_keeps_bc(tmp_path: Path, downloads: list[str]) -> None:
    """torch 后端不受影响：bc 照旧往下走（行为零变化）。"""
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("bc"), work_dir=tmp_path, backend="torch")
    assert downloads == [JID]


def test_unknown_backend_is_rejected(tmp_path: Path, downloads: list[str]) -> None:
    """取值域外的 `--backend` 响亮拒收，且**任何下载之前**（与 `resolve_ppo_backend` 同判据）。"""
    with pytest.raises(ProtocolError, match="未知 PPO backend"):
        worker.run_job(
            "http://hub", "tok", _job("ppo"), work_dir=tmp_path, backend="tensorflow"
        )
    assert downloads == []


# ────────────────────── ③ `--device auto` 在 Paddle 后端问 Paddle ──────────────────────


def _drive_ppo_to_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    probe: object,
    device: str = "auto",
) -> dict:
    """把 kind=ppo 推到训练核调用点（下载/落地/权重解析全部打桩），返回调用关键字。"""
    job_dir = tmp_path / JID
    job_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        worker,
        "_ensure_payload",
        lambda *_a, **_k: SimpleNamespace(
            job_dir=job_dir,
            shard_dirs=[],
            dl_sec=0.0,
            unpack_sec=0.0,
            payload_nbytes=0,
            staged=False,
        ),
    )
    monkeypatch.setattr(
        worker,
        "_ensure_code",
        lambda *_a, **_k: SimpleNamespace(
            code_root=job_dir / "code",
            code_cache_dir=tmp_path / "cc",
            blob_root=tmp_path / "blob",
        ),
    )
    monkeypatch.setattr(worker, "_resolve_weights", lambda **_k: (None, "payload", 0))
    monkeypatch.setattr(worker, "_ACTIVE_CODE_SHA", None)
    monkeypatch.setattr(worker, "paddle_cuda_available", probe)
    monkeypatch.setattr(worker, "validate_result", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_persist_result", lambda *_a, **_k: None)
    seen: dict = {}

    def _core(*_a: object, **kw: object) -> tuple[dict, None]:
        seen.update(kw)
        return {"job_id": JID}, None

    monkeypatch.setattr(worker, "run_training_core", _core)
    worker.run_job(
        "http://hub", "tok", _job("ppo"), work_dir=tmp_path, device=device, backend="paddle"
    )
    return seen


def test_paddle_auto_device_asks_paddle_for_gpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _drive_ppo_to_core(monkeypatch, tmp_path, probe=lambda: True)
    assert seen["device"] == "cuda", "Paddle 有 GPU 时 auto 必须落 cuda（cuda→gpu 由 paddle 归一）"
    assert seen["backend"] == "paddle"


def test_paddle_auto_device_falls_back_to_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _drive_ppo_to_core(monkeypatch, tmp_path, probe=lambda: False)
    assert seen["device"] == "cpu"
    assert seen["backend"] == "paddle"


def test_paddle_keeps_an_explicit_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """显式 `--device` 不经过 Paddle 探测（auto 才需要替 normalize 问一次）。"""

    def _boom() -> bool:
        raise AssertionError("显式设备不该探测 Paddle GPU")

    seen = _drive_ppo_to_core(monkeypatch, tmp_path, probe=_boom, device="cpu")
    assert seen["device"] == "cpu"


# ─────────────── ④ 载荷通道：显式参数（argv → worker_loop）/ env 无效果 ───────────────


def test_backend_env_has_no_effect(
    tmp_path: Path, downloads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 行为判据（2026-10-27 评审：换掉原「扫源码里有没有 PPO_BACKEND 字样」那条）。

    名字扫描把**注释里的历史记录**也算命中（守卫越写越像文字狱），而它要防的事只有一件
    —— **没人传的隐形开关**（2026-09-21：`--ppo` 漏传 ⇒ 悄悄本机 CPU 跑 13 小时）。
    那件事的行为面 = 光设一个环境变量，`run_job` 的判定一个字都不变：`paddle` 后端只收
    kind=ppo ⇒ 设了同名 env 却让 BC 活照常往下走（走到取 payload 那一步）就是硬证据。
    反向同样成立：谁哪天把 env 读回来，这条当场红（BC 会被拒）。
    """
    monkeypatch.setenv("NN_PPO_BACKEND", "paddle")
    with pytest.raises(_DownloadedError):
        worker.run_job("http://hub", "tok", _job("bc"), work_dir=tmp_path)
    assert downloads == [JID]


def test_cli_backend_flag_reaches_the_worker_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--backend paddle` → `worker_loop(backend='paddle')`（子进程分支 = 云机走的那条）。

    为什么钉这条：载荷必须**显式**（与 `--device`/`--epoch-mult` 同路），而 supervisor 的
    热替换重放的是 `sys.argv[1:]`（`--backend` 在其中）⇒ 重拉后照样成立。
    """
    seen: dict[str, object] = {}

    def _fake_loop(*_a: object, **k: object) -> int:
        seen.update(k)
        raise SystemExit(0)

    monkeypatch.setattr(worker, "worker_loop", _fake_loop, raising=True)
    monkeypatch.setenv("REMOTE_WORKER_CHILD", "1")
    tok = tmp_path / "t"
    tok.write_text("T" * 32, encoding="utf-8")
    monkeypatch.setattr(
        worker.sys,
        "argv",
        [
            "remote.remote_worker",
            "--poll",
            "http://x",
            "--token-file",
            str(tok),
            "--backend",
            "paddle",
        ],
    )
    with pytest.raises(SystemExit):
        worker.main()
    assert seen["backend"] == "paddle"

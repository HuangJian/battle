"""tests/remote/test_weights_delta_land.py — R1/M3 的接线（`plan/wire-diet.plan.md` §3 R1）。

钉住两件事（各对应一条「没有它会静默出错」）：

  ① **hub 侧重建落地**（`hub_client._land_weights_delta`）：delta 容器 + 本机那份 init 权重 ⇒ 新的
     weights_json 字节；**exact** 档（容器 `base_sha` == 本机字节的 sha）逐字节对账基准，
     **identity** 档（worker 用自己那份 f32 副本当基准）也照常重建在**本机**那条链上 —— 两档都要
     能在日志里分开报，但行为面只有「重建结果」这一件可断言；缺 base / 容器坏 / 结构不符一律抛
     （绝不把 delta 当权重落盘）。
  ② **发布端形态位**（`publish_job`）：`slim` + 真有 init 权重 ⇒ 写 `manifest.w_fmt`；没有 ⇒
     **不添键**（缺键 = 旧形态，对外发布路径逐字节不变）。

worker 侧那几行（形态位命中 ⇒ `pack_weights_delta`，基准 = 本轮载入的 `init_weights.json`）在
`tests/remote/test_train_core_split.py` 里用结构守卫钉住 —— `run_training_core` 全链要真 torch +
真课程，不在单测里跑（同该文件既有口径）。容器本身的往返/决定性/误差界/拒收在
`tests/remote/test_weights_delta.py`。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import weights_delta as wd
from remote.hub_client import _land_weights_delta

SHAPES = {"conv.w": (6, 3, 3, 3), "fc.b": (16,), "head.w": (16, 165)}


def _weights_json(params: dict[str, np.ndarray]) -> bytes:
    return json.dumps(
        {
            "format": "nn-weights-json",
            "version": 1,
            "schema_major": 3,
            "arch": {"hidden": 128},
            "num_params": int(sum(int(p.size) for p in params.values())),
            "params": {
                n: {
                    "shape": list(p.shape),
                    "data": base64.b64encode(
                        np.ascontiguousarray(p, dtype="<f4").tobytes()
                    ).decode("ascii"),
                }
                for n, p in params.items()
            },
        },
        indent=2,
    ).encode("utf-8")


def _arr(entry: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(entry["shape"])


def _train_step(base: dict[str, np.ndarray], *, seed: int, amp: float = 2e-3) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: (p + rng.standard_normal(p.shape).astype(np.float32) * amp).astype(np.float32) for n, p in base.items()}


def _quiet(_msg: str = "") -> None:
    return None


def _scale_of(blob: bytes) -> float:
    """容器头里的 scale（测试自己读一份，好把断言挂在**容器自述的量**上，不靠猜）。"""
    hdr_len = int.from_bytes(blob[4:8], "big")
    return float(json.loads(blob[8 : 8 + hdr_len].decode("utf-8"))["scale"])


def _params(seed: int, *, amp: float = 0.2) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: rng.standard_normal(s).astype(np.float32) * amp for n, s in SHAPES.items()}


def test_land_rebuilds_on_the_hub_chain(tmp_path: Path) -> None:
    """① exact 档：容器基准 = 本机字节 ⇒ 重建结果 = 训练后的权重（量化界内），且落到 nn-weights-json。"""
    hub_base = _params(1)
    hub_path = tmp_path / "init_weights.json"
    hub_path.write_bytes(_weights_json(hub_base))
    new = _train_step(hub_base, seed=2)
    blob = wd.pack_weights_delta(
        hub_path.read_bytes(), _weights_json(new), base_sha=hashlib.sha256(hub_path.read_bytes()).hexdigest()
    )
    got = json.loads(_land_weights_delta(blob, base_path=hub_path, log=_quiet).decode("utf-8"))
    assert got["format"] == "nn-weights-json" and got["num_params"] == json.loads(_weights_json(new))["num_params"]
    worst = 0.0
    biggest = 0.0
    for n, p in new.items():
        worst = max(worst, float(np.abs(_arr(got["params"][n]) - p).max()))
        biggest = max(biggest, float(np.abs(p).max()))
    scale = _scale_of(blob)
    # 硬上界 = 量化 (scale/2) + 重建那一步的 fp32 叠加舍入（≤ 1 ulp）。
    assert worst <= scale / 2 + float(np.spacing(np.float32(biggest))), (
        f"重建误差 {worst} 超出 scale/2={scale / 2}"
    )


def test_land_identity_band_rebuilds_the_workers_delta_on_our_base(tmp_path: Path) -> None:
    """① identity 档（M0 身份命中）：worker 的基准不是我们的字节 ⇒ 仍照常重建，但加的是**它的 Δ**。

    语义就是 plan §12 定案 1 的那一条：hub 只能把自己那条链当基准 ⇒ 存档与精确链之间留下
    一轮量化误差量级的差（`B_n = B_{n-1} + Q(A_n − A_{n-1})`，此处 B 与 A 起点不同）。
    """
    hub_base = _params(1)
    worker_base = _train_step(hub_base, seed=9, amp=1e-2)  # worker 自己那条链的起点
    hub_path = tmp_path / "init_weights.json"
    hub_path.write_bytes(_weights_json(hub_base))
    worker_new = _train_step(worker_base, seed=3)
    blob = wd.pack_weights_delta(
        _weights_json(worker_base), _weights_json(worker_new), base_sha="b" * 64  # ≠ 本机 sha
    )
    got = json.loads(_land_weights_delta(blob, base_path=hub_path, log=_quiet).decode("utf-8"))
    # 期望 = 本机 base + (worker_new − worker_base)
    for n in SHAPES:
        want = hub_base[n] + (worker_new[n] - worker_base[n])
        worst = float(np.abs(_arr(got["params"][n]) - want).max())
        assert worst <= 1e-5, f"{n}：identity 档重建偏离 Δ 语义 {worst}"


def test_land_refuses_without_base(tmp_path: Path) -> None:
    """① base 不在（prune / 换 run 目录）⇒ `ProtocolError` —— 绝不把 delta 当权重落盘。

    异常族在 `remote/delta_land` 里统一（同族两个异常的重试分类等价，理由见模块头）。
    """
    base = _params(1)
    blob = wd.pack_weights_delta(
        _weights_json(base), _weights_json(_train_step(base, seed=2)), base_sha="a" * 64
    )
    with pytest.raises(ProtocolError):
        _land_weights_delta(blob, base_path=tmp_path / "missing.json", log=_quiet)
    with pytest.raises(ProtocolError):
        _land_weights_delta(blob, base_path="", log=_quiet)


def test_land_propagates_broken_container(tmp_path: Path) -> None:
    """① 容器坏 / 基准结构不符 ⇒ `ProtocolError`（响亮，不静默降级）。"""
    base = _params(1)
    hub_path = tmp_path / "init_weights.json"
    hub_path.write_bytes(_weights_json(base))
    new = _train_step(base, seed=2)
    blob = wd.pack_weights_delta(
        hub_path.read_bytes(), _weights_json(new), base_sha=hashlib.sha256(hub_path.read_bytes()).hexdigest()
    )
    with pytest.raises(ProtocolError):
        _land_weights_delta(b"XXXX" + blob[4:], base_path=hub_path, log=_quiet)
    # 基准换一份「少一个参数」的 ⇒ 结构闸拦下
    thin = {n: p for n, p in base.items() if n != "fc.b"}
    thin_path = tmp_path / "thin.json"
    thin_path.write_bytes(_weights_json(thin))
    with pytest.raises(ProtocolError):
        _land_weights_delta(blob, base_path=thin_path, log=_quiet)


def test_land_falls_back_to_the_old_form_when_the_sender_ignores_the_bit(tmp_path: Path) -> None:
    """名实不符那一档：形态位写的是 delta，但回传的是**整份 JSON** ⇒ 按旧形态落盘 + 记一行。

    为什么容忍：发布端与节点代可能错位（`code.zip` 热替换 / 离线缓存的旧副本），旧代节点不该让
    整条腿的 job 全废。但「名实不符」能逐字节判、且必须**不静默**（日志面）；真正坏掉的字节
    （既不是容器也不是合法 JSON）仍拒收 —— 两条都在这个用例里钉住。
    """
    base = _params(1)
    hub_path = tmp_path / "init_weights.json"
    full = _weights_json(_train_step(base, seed=2))
    hub_path.write_bytes(_weights_json(base))
    lines: list[str] = []
    gone = b"neither container nor json"
    assert _land_weights_delta(full, base_path=hub_path, log=lines.append) == full, (
        "旧形态必须**逐字节**原样落盘（不许重打/重编）"
    )
    assert _land_weights_delta(gone, base_path=hub_path, log=lines.append) == gone, (
        "这一档与旧形态逐字节同行为（本 plan 不动旧路径的宽容度）"
    )
    assert lines, "名实不符必须留一行日志（不静默降级）"
    # 真容器坏掉仍然拒收（这才是本 plan 要守的那条线）
    blob = wd.pack_weights_delta(
        hub_path.read_bytes(), _weights_json(_train_step(base, seed=2)), base_sha="a" * 64
    )
    with pytest.raises(ProtocolError):
        _land_weights_delta(blob[: len(blob) - 2], base_path=hub_path, log=lines.append)


def _publish(tmp_path: Path, *, it: int = 1, wfile: str | Path | None = None) -> dict:
    """`wfile=None` = 现造一份真的 init 权重；`wfile=""` = **没有** init 字节（首轮 / 旧路径）。"""
    from remote.hub_client import publish_job

    if wfile is None:
        wfile = tmp_path / "init_weights.json"
        wfile.write_bytes(_weights_json(_params(5)))
    jsonl = tmp_path / "training_log.jsonl"
    if not jsonl.exists():
        jsonl.write_text("", encoding="utf-8")
    return publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=jsonl,
        run_id="r1",
        it=it,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='{"reward":{"formula":"score"}}',
        course_fp="c" * 64,
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        init_weights_path=str(wfile),
        ckpt_remote_dir=None,
        slim=True,
        log=_quiet,
    )


def test_publish_writes_w_fmt_only_when_init_weights_exist(tmp_path: Path) -> None:
    """② 有 init 权重 + slim ⇒ 写形态位（且 `init_weights_fp` 就是它的 sha）；否则不添键。"""
    m = _publish(tmp_path)
    assert str(m.get("w_fmt") or "") == wd.W_DELTA_FMT
    assert str(m.get("init_weights_fp") or ""), "有 init 权重的发布必须同时给 init_weights_fp（基准的键）"
    # 盘上那份权威 manifest 也得带形态位（worker 读的是它）
    on_disk = json.loads(
        (tmp_path / "jobs" / m["job_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert str(on_disk.get("w_fmt") or "") == wd.W_DELTA_FMT
    # 首轮 / 无 init 权重（`init_weights_path=""`）⇒ 缺键 = 旧形态，且 fp 退占位符
    no_base = _publish(tmp_path, it=2, wfile="")
    assert str(no_base.get("w_fmt") or "") == "", "没有 init 字节不许写形态位"
    assert str(no_base.get("init_weights_fp") or "") == "bc"

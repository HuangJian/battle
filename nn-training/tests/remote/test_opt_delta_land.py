"""tests/remote/test_opt_delta_land.py — M4 切片 2 的接线（`plan/wire-diet.plan.md` §3 R3 / §12）。

钉住两件事（各对应一条「没有它会静默出错」）：

  ① **hub 侧重建落地**（`opt_delta.land_opt_delta`，`hub_client` 在 `verify_and_land` 里局部调它）：
     delta 容器 + 上一轮 landed 的 base ⇒ 写
     `ckpt_dir/opt.pt` 并押回**权威 tar 字节**（下一轮 publish 的 `opt_sha` 由它算，只装 `opt.pt`）；
     重建结果 = 新状态在 f16 界内；**缺 base / 容器坏 / 结构不符一律抛**（绝不把 delta 当权重落盘）；
  ② **发布端形态位**（`publish_job`）：`slim` + 真有 opt base ⇒ 写 `manifest.opt_fmt`；没有 base ⇒
     **不添键**（对外发布路径的 manifest 逐字节不变，缺键 = 旧形态）。

worker 侧那 10 行（形态位命中 ⇒ `pack_opt_delta`，基准 = 本轮载入的 `opt_raw`）在
`tests/remote/test_train_core_split.py` 里用 AST 守卫钉住 —— `run_training_core` 全链要真 torch +
真课程，不在单测里跑（同该文件既有口径）。
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import opt_delta as od
from remote.hub_client import _extract_tar
from remote.opt_delta import land_opt_delta as _land_opt_delta


def _state(seed: int, *, n: int = 2, elems: int = 64) -> dict:
    """一份确定性的 `opt.state_dict()` 形状（含 `step` 与 `param_groups`）。"""
    g = torch.Generator().manual_seed(seed)
    st = {
        str(pid): {
            "step": torch.tensor([5.0 + pid]),
            "exp_avg": torch.randn(elems, generator=g) * 3e-2,
            "exp_avg_sq": torch.rand(elems, generator=g) * 1e-4,
        }
        for pid in range(n)
    }
    return {"state": st, "param_groups": [{"lr": 3e-4, "betas": [0.9, 0.95], "params": [0, 1]}]}


def _leaf_map(obj, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_leaf_map(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_leaf_map(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def _quiet(_msg: str = "") -> None:
    return None


def _write_base(traj: Path, it: int, state: dict) -> Path:
    d = traj / f"it{it}" / "ppo_ckpt_remote"
    d.mkdir(parents=True, exist_ok=True)
    torch.save(state, d / "opt.pt")
    return d


def test_land_rebuilds_new_state_and_returns_authority_tar(tmp_path: Path) -> None:
    """① delta 容器 + base ⇒ 写新 `opt.pt`（f16 界内）+ 返回只装 `opt.pt` 的权威 tar。"""
    base, new = _state(1), _state(2)
    traj = tmp_path / "traj"
    _write_base(traj, 0, base)
    blob = od.pack_opt_delta(base, new, base_sha="a" * 64)
    out = traj / "it1" / "ppo_ckpt_remote"
    out.mkdir(parents=True, exist_ok=True)
    tar = _land_opt_delta(
        blob, traj_dir=traj, it=1, ckpt_dir=out, extract=_extract_tar, log=_quiet
    )
    got = torch.load(out / "opt.pt", map_location="cpu")
    want_leaves, got_leaves = _leaf_map(new), _leaf_map(got)
    assert sorted(want_leaves) == sorted(got_leaves)
    worst = 0.0
    for path, want in want_leaves.items():
        have = got_leaves[path]
        if not isinstance(want, torch.Tensor):  # param_groups 里的标量（lr/betas）⇒ 逐值相等
            assert have == want, path
            continue
        assert isinstance(have, torch.Tensor), path
        if path.endswith("step"):
            # `step` 是**非连续量**（Adam 的窗口/步数）：容器按 exact 搬，量化会改语义
            assert torch.equal(have, want), f"{path} 必须逐值精确"
            continue
        worst = max(worst, float((have.float() - want.float()).abs().max()))
    assert worst <= 6e-4, f"重建误差 {worst} 超出 f16 界（plan §3 R3 实测 max 5.43e-5）"
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:") as tf:
        assert tf.getnames() == ["opt.pt"], "权威 tar 只装 opt.pt（下一轮 publish 的 opt_sha 由它算）"


def test_land_refuses_without_base(tmp_path: Path) -> None:
    """① base 不在（prune / 换 traj）⇒ `ProtocolError` —— 绝不把 delta 当权重落盘。

    异常族在 `remote/delta_land` 里统一（拿不到 base = 解读不了这份 result，与容器坏同一族），
    并由该模块的 L0 位置约束（同族两个异常的重试分类等价，理由见模块头）。
    """
    base, new = _state(1), _state(2)
    traj = tmp_path / "traj"
    blob = od.pack_opt_delta(base, new, base_sha="a" * 64)
    with pytest.raises(ProtocolError):
        _land_opt_delta(
            blob, traj_dir=traj, it=1, ckpt_dir=traj / "it1", extract=_extract_tar, log=_quiet
        )


def test_land_propagates_broken_container(tmp_path: Path) -> None:
    """① 容器坏 / 基准结构不符 ⇒ `ProtocolError`（响亮，不静默降级）。"""
    base, new = _state(1), _state(2)
    traj = tmp_path / "traj"
    _write_base(traj, 0, base)
    blob = od.pack_opt_delta(base, new, base_sha="a" * 64)
    with pytest.raises(ProtocolError):
        _land_opt_delta(
            b"XXXX" + blob[4:], traj_dir=traj, it=1, ckpt_dir=traj / "it1", extract=_extract_tar, log=_quiet
        )
    thin = _state(9, n=1)
    _write_base(traj, 2, thin)  # 换一份「少一组参数」的 base ⇒ 结构闸拦下
    with pytest.raises(ProtocolError):
        _land_opt_delta(
            blob, traj_dir=traj, it=3, ckpt_dir=traj / "it3", extract=_extract_tar, log=_quiet
        )


def test_land_dispatches_on_the_magic_not_the_bit(tmp_path: Path) -> None:
    """名实不符那一档：形态位写的是 delta，但回传的不是 BOD1 容器 ⇒ 按**旧形态**解包 + 记一行。

    容忍的理由与 `weights_delta` 同条（发布端与节点代可能错位）；这里额外钉住**异常分类**：
    名实不符**且解包也失败**必须转成 `ProtocolError`（tarfile 原生异常不在重试白名单里，
    泄出去会落到 loop_core 的通用兜底 —— 2026-09-16 事故的形状）。
    """
    out = tmp_path / "it1" / "ppo_ckpt_remote"
    out.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[bytes, Path]] = []
    lines: list[str] = []

    def _fake_extract(blob: bytes, dest: Path) -> None:
        calls.append((blob, dest))

    blob = b"not a container"
    got = _land_opt_delta(
        blob, traj_dir=tmp_path, it=1, ckpt_dir=out, extract=_fake_extract, log=lines.append
    )
    assert got is blob or got == blob, "旧形态必须原样交回去（不重建）"
    assert calls == [(blob, out)], "旧形态走解包腿，目标目录 = ckpt_dir"
    assert lines, "名实不符必须留一行日志（不静默降级）"

    def _broken(_blob: bytes, _dest: Path) -> None:
        raise tarfile.ReadError("truncated")

    with pytest.raises(ProtocolError):
        _land_opt_delta(
            blob, traj_dir=tmp_path, it=1, ckpt_dir=out, extract=_broken, log=lines.append
        )


def _publish(tmp_path: Path, *, it: int = 1, ckpt_dir: Path | None = None, slim: bool = True) -> dict:
    from remote.hub_client import publish_job

    wfile = tmp_path / "init_weights.json"
    if not wfile.exists():
        wfile.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
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
        ckpt_remote_dir=ckpt_dir,
        slim=slim,
        log=_quiet,
    )


def test_publish_writes_opt_fmt_only_when_a_base_exists(tmp_path: Path) -> None:
    """② 有 base + slim ⇒ 写形态位；没有 base ⇒ 不添键（对外发布路径逐字节不变）。"""
    base = _state(1)
    have = _write_base(tmp_path / "traj", 0, base)
    with_base = _publish(tmp_path, it=1, ckpt_dir=have)
    assert str(with_base.get("opt_fmt") or "") == od.OPT_DELTA_FMT
    assert str(with_base.get("opt_sha") or ""), "有 base 的发布必须同时给 opt_sha（重建链的键）"
    no_base = _publish(tmp_path, it=9, ckpt_dir=None)
    assert str(no_base.get("opt_fmt") or "") == "", "没有 base 不许写形态位（缺键 = 旧形态）"
    # 盘上那份权威 manifest 也得带形态位（worker 读的是它）
    on_disk = json.loads(
        (tmp_path / "jobs" / with_base["job_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert str(on_disk.get("opt_fmt") or "") == od.OPT_DELTA_FMT

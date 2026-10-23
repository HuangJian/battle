"""tests/remote/test_round_forms.py —— 轮形态（2026-10-23，`plan/anchor-full-rounds`）。

用户口径：**首轮和 eval 轮发整份 weight 和 opt，其它轮发 delta。** 本例钉住那条口径在
**写盘那一刻**的落点（`remote/round_forms.RoundForms`），以及它赖以成立的三条判据：

  ① **形态只由一个判据定**（`deliver_wire.is_full_round`：首轮 ∪ `it % eval_every == 0`）——
     任何第二份「是不是锚点轮」的判断都是这条链上的分叉点；
  ② **基准只能是「盘上最近一份整份」**（hub 的落地器按 sha256 逐字节找基准，而重打出来的
     tar 字节随 mtime 变 ⇒ 「上一轮重建出来的那份」在 hub 侧永远对不上）；
  ③ **回落一律保留整份**（缺基准 / 结构变化 / 任何异常）：形态改不动只是「这一轮大一点」，
     而写不出一轮、或写出一份 hub 解不开的容器，是整段死。

tar 的打包/解包在用例里**独立重实现**（`_tar_bytes` / `_load_tar`），不复用被测模块的实现
（AGENTS §8：判据要能被另一份实现复现）。
"""

from __future__ import annotations

import hashlib
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

import remote.deliver_wire as deliver_wire
import remote.delta_files as delta_files
import remote.opt_delta as od
import remote.opt_standard as os_std
from remote.artifacts import ArtifactStore
from remote.round_forms import RoundForms


def _state(seed: int, *, n: int = 2, elems: int = 32) -> dict:
    """一份确定性的「`opt.state_dict()` 那一族」状态（与 `test_opt_standard._state` 同族）。"""
    g = torch.Generator().manual_seed(seed)
    st = {
        str(pid): {
            "step": torch.tensor([5.0 + pid]),
            "exp_avg": torch.randn(elems, generator=g) * 3e-2,
            "exp_avg_sq": torch.rand(elems, generator=g) * 1e-4,
        }
        for pid in range(n)
    }
    return {
        "state": st,
        "param_groups": [{"lr": 3e-4, "betas": [0.9, 0.95], "eps": 1e-8, "params": list(range(n))}],
    }


def _tar_bytes(state: dict, tmp: Path, name: str) -> bytes:
    """state → 单件 tar 字节（独立重实现：`torch.save` + `tarfile` 手打，成员恒为 `opt.pt`）。"""
    d = tmp / f"_tar-{name}"
    d.mkdir(parents=True, exist_ok=True)
    torch.save(state, d / "opt.pt")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tfb:
        tfb.add(d / "opt.pt", arcname="opt.pt")
    return buf.getvalue()


def _load_tar(tar_bytes: bytes, tmp: Path, tag: str) -> dict:
    """tar 字节 → state（独立重实现：手解包 + `torch.load`，不碰被测模块）。"""
    d = tmp / f"_un-{tag}"
    d.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(d, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(d)
    state: dict = torch.load(d / "opt.pt", map_location="cpu")
    return state


def _assert_close(got: dict, want: dict, *, tol: float = 1e-3) -> None:
    """f16 量化界内一致；`step` 逐值精确（量化 `step` 是改语义，不是量化误差）。"""
    for pid, leaf in want["state"].items():
        for k, v in leaf.items():
            g = got["state"][pid][k]
            if k == "step":
                assert torch.equal(g, v), f"step 必须逐值精确（{pid}）"
            else:
                assert float((g - v).abs().max()) <= tol, f"{pid}.{k} 超出量化界"
    assert got["param_groups"] == want["param_groups"]


class _Course:
    """最小的课程面（`eval_every` 是本 plan 唯一的输入）。"""

    def __init__(self, eval_every: int) -> None:
        self.eval_every = eval_every


def _store(root: Path, forms: RoundForms | None) -> ArtifactStore:
    """产物目录 + 形态钩子（日志跟 `forms` 同一支：用例要从那里读「回落」那几行）。"""
    return ArtifactStore(
        root,
        run_id="run-x",
        log=forms.log if forms is not None else (lambda _m: None),
        after_write=forms.after_write if forms is not None else None,
    )


def _write_round(store: ArtifactStore, it: int, state: dict, tmp: Path) -> dict:
    """落一轮（权重字节内容与形态判据无关：这里只钉 opt 那一半 + 账本开销）。"""
    return store.checkpoint(
        it,
        weights_json=json.dumps({"it": int(it)}).encode("utf-8"),
        opt_tar=_tar_bytes(state, tmp, f"it-{int(it)}"),
        row={"it": int(it)},
    )


# ------------------------------------------------------------------ 判据


def test_is_full_round_is_first_plus_eval_multiples() -> None:
    """① 首轮 ∪ eval 倍数；`eval_every <= 0` ⇒ 只有首轮（调用方本不该装这个判据）。"""
    assert deliver_wire.is_full_round(1, first_it=1, eval_every=5) is True
    assert deliver_wire.is_full_round(5, first_it=1, eval_every=5) is True
    assert deliver_wire.is_full_round(10, first_it=1, eval_every=5) is True
    assert deliver_wire.is_full_round(6, first_it=1, eval_every=5) is False
    # 续跑段：`first_it` 是本 run 的第一轮（不是本会话第一轮）——它照样要整份。
    assert deliver_wire.is_full_round(237, first_it=237, eval_every=5) is True
    assert deliver_wire.is_full_round(3, first_it=1, eval_every=0) is False
    assert deliver_wire.is_full_round(1, first_it=1, eval_every=0) is True


def test_for_store_refuses_courses_whose_every_round_is_an_anchor() -> None:
    """`eval_every < 2`（含 `CourseSpec` 缺省 1）⇒ **不装** + 一行 WARN（不许静默零收益）。"""
    lines: list[str] = []
    # 判据是行为（返回 `None`）与「**说了一行**」；措辞不是契约（文本断言不进账本）。
    assert RoundForms.for_store("tmp/x", plan={"start_it": 1}, course=None, log=lines.append) is None
    assert len(lines) == 1, lines

    lines.clear()
    assert (
        RoundForms.for_store("tmp/x", plan={"start_it": 1}, course=_Course(1), log=lines.append)
        is None
    )
    assert len(lines) == 1, lines

    lines.clear()
    forms = RoundForms.for_store(
        "tmp/x", plan={"start_it": 3}, course=_Course(5), log=lines.append
    )
    assert forms is not None and forms.first_it == 3 and forms.eval_every == 5
    assert len(lines) == 1, lines


def test_first_it_comes_from_the_artifacts_not_the_segment(tmp_path: Path) -> None:
    """`first_it` = 产物目录里最小的 `it-*`（续跑时仍是本 run 的第一轮）。"""
    (tmp_path / "it-237").mkdir()
    (tmp_path / "it-240").mkdir()
    forms = RoundForms.for_store(tmp_path, plan={"start_it": 240}, course=_Course(5))
    assert forms is not None and forms.first_it == 237


def test_prepare_manifest_pops_w_fmt_on_anchors_only() -> None:
    """驱动侧：锚点轮弹 `w_fmt`（worker 回整份）；其余写 `W_DELTA_FMT`；`opt_fmt` 一律弹掉。"""
    forms = RoundForms("tmp/x", first_it=1, eval_every=5)
    anchor = {"w_fmt": "delta-i16", "opt_fmt": "delta-f16"}
    forms.prepare_manifest(anchor, 5)
    assert anchor == {}, "锚点轮必须让 worker 回整份（两个形态位都不该在）"
    other = {"w_fmt": "delta-i16", "opt_fmt": "delta-f16"}
    forms.prepare_manifest(other, 6)
    assert other == {"w_fmt": "delta-i16"}, "非锚点轮：权重 delta，opt 由写盘侧定"


# ------------------------------------------------------------------ 写盘形态


def test_anchor_rounds_keep_full_and_the_rest_become_containers(tmp_path: Path) -> None:
    """D1/D2/D3：`eval_every=2` 的 6 轮 ⇒ it1/it2/it4/it6 整份、it3/it5 容器；账本按**最终**字节。"""
    root = tmp_path / "art"
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2))
    assert forms is not None
    store = _store(root, forms)
    states = {it: _state(100 + it) for it in range(1, 7)}
    rows = {it: _write_round(store, it, states[it], tmp_path) for it in states}

    for it, state in states.items():
        p = root / f"it-{it:03d}" / "opt.tar"
        is_delta = delta_files.any_container_file(p)
        assert is_delta is (it in (3, 5)), f"it{it} 的形态不对（is_delta={is_delta}）"
        assert rows[it]["opt_bytes"] == p.stat().st_size, "账本的 opt_bytes 必须是改写后的字节数"
        raw = p.read_bytes()
        if is_delta:
            assert raw[:4] == od.OPT_DELTA_MAGIC
            # 基准 = 最近的整份（it2 之于 it3、it4 之于 it5）——不是「上一轮重建出来的那份」。
            assert od.base_sha_of(raw) == hashlib.sha256(
                (root / f"it-{it - 1:03d}" / "opt.tar").read_bytes()
            ).hexdigest()
        else:
            _assert_close(_load_tar(raw, tmp_path, f"full-{it}"), state, tol=0.0)

    for it in (1, 2, 4, 6):
        _assert_close(_load_tar((root / f"it-{it:03d}" / "opt.tar").read_bytes(), tmp_path, f"a{it}"), states[it], tol=0.0)


def test_anchor_rounds_are_loadable_alone(tmp_path: Path) -> None:
    """D4：删掉所有非锚点轮 ⇒ 每个 eval 轮仍能**单独**装载（容器一个都不剩）。

    这正是「花 60 MB 买 eval 轮独立可装载」的判据：链断了只损失 ≤ N 轮，不是整段。
    """
    root = tmp_path / "art"
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2))
    store = _store(root, forms)
    states = {it: _state(200 + it) for it in range(1, 7)}
    for it, state in states.items():
        _write_round(store, it, state, tmp_path)
    for it in (3, 5):
        (root / f"it-{it:03d}" / "opt.tar").unlink()  # 非锚点轮全删（模拟「只留下落点」）

    for it in (1, 2, 4, 6):
        p = root / f"it-{it:03d}" / "opt.tar"
        assert not delta_files.any_container_file(p), f"it{it} 应该是整份"
        landed = os_std.ensure_standard_opt(p, log=lambda _m: None)
        assert landed == p and p.read_bytes() == landed.read_bytes(), "整份零成本：一个字节不动"
        _assert_close(_load_tar(p.read_bytes(), tmp_path, f"alone-{it}"), states[it], tol=0.0)


def test_the_container_lands_back_to_the_round_it_was_written_from(tmp_path: Path) -> None:
    """容器的解出物 == 那一轮的状态（读侧 `ensure_standard_opt` 用盘上锚点当基准）。"""
    root = tmp_path / "art"
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2))
    store = _store(root, forms)
    states = {it: _state(300 + it) for it in range(1, 4)}
    for it, state in states.items():
        _write_round(store, it, state, tmp_path)

    p = root / "it-003" / "opt.tar"
    assert delta_files.any_container_file(p)
    landed = os_std.ensure_standard_opt(p, log=lambda _m: None)
    _assert_close(_load_tar(landed.read_bytes(), tmp_path, "landed"), states[3])
    assert delta_files.any_container_file(root / "it-003" / "opt.delta"), "两版都留（名字自述形态）"


def test_missing_base_falls_back_to_full_with_a_warning(tmp_path: Path) -> None:
    """D5：删掉最近整份 ⇒ 这一轮保留**整份** + 一行 WARN（不抛、不丢字节）。"""
    root = tmp_path / "art"
    lines: list[str] = []
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2), log=lines.append)
    store = _store(root, forms)
    _write_round(store, 1, _state(401), tmp_path)
    (root / "it-001" / "opt.tar").unlink()  # 唯一的候选整份没了
    row = _write_round(store, 3, _state(403), tmp_path)  # it3 是非锚点轮

    p = root / "it-003" / "opt.tar"
    assert not delta_files.any_container_file(p), "缺基准必须回落整份（宁大不解不开）"
    assert row["opt_bytes"] == p.stat().st_size
    assert len(lines) == 2, lines  # 开段一行 + 回落一行（响亮面：不能默默写大件）


def test_structure_change_falls_back_to_full(tmp_path: Path) -> None:
    """D6：`param_groups`/state 结构变了 ⇒ `pack_opt_delta` 拒发 ⇒ 该轮写整份（不是失败）。"""
    root = tmp_path / "art"
    lines: list[str] = []
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2), log=lines.append)
    store = _store(root, forms)
    _write_round(store, 1, _state(501), tmp_path)
    wider = _state(503, n=3)  # 基准里没有第三个参数组 ⇒ 结构性不一致
    row = _write_round(store, 3, wider, tmp_path)

    p = root / "it-003" / "opt.tar"
    assert not delta_files.any_container_file(p), "结构不一致必须保留整份（拒发而不是猜基准）"
    assert row["opt_bytes"] == p.stat().st_size
    assert len(lines) == 2, lines  # 开段一行 + 钩子失败一行（`artifacts` 只记不抛）
    _assert_close(_load_tar(p.read_bytes(), tmp_path, "kept"), wider, tol=0.0)


def test_none_forms_is_the_old_behaviour(tmp_path: Path) -> None:
    """不装钩子（`eval_every<2` / 课程缺失）⇒ 一个字节都不动：opt 每轮整份 = 今天的行为。"""
    root = tmp_path / "art"
    store = _store(root, None)
    states = {it: _state(600 + it) for it in range(1, 4)}
    for it, state in states.items():
        _write_round(store, it, state, tmp_path)
    for it, state in states.items():
        p = root / f"it-{it:03d}" / "opt.tar"
        assert not delta_files.any_container_file(p)
        _assert_close(_load_tar(p.read_bytes(), tmp_path, f"old-{it}"), state, tol=0.0)


def test_after_write_is_idempotent(tmp_path: Path) -> None:
    """容器已经在了 ⇒ 第二次调用不再动它（补传/重放会把同一轮喂进来）。"""
    root = tmp_path / "art"
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2))
    assert forms is not None
    store = _store(root, forms)
    _write_round(store, 1, _state(701), tmp_path)
    _write_round(store, 3, _state(703), tmp_path)
    p = root / "it-003" / "opt.tar"
    before = p.read_bytes()
    forms.after_write(3, root / "it-003")
    forms.after_write(3, root / "it-003")
    assert p.read_bytes() == before


# ------------------------------------------------------------------ 读数


def test_note_opt_wire_counts_only_measured_bytes() -> None:
    """S4：省字节只从**实际发出去的字节**量；没见过整份就不计（不编基数）。"""
    zero = deliver_wire.OPT_WIRE_ZERO
    assert zero == ("none", 0, 0)
    st = deliver_wire.note_opt_wire(zero, "delta", 154_000)
    assert st == ("delta", 0, 0), "参照没见过 ⇒ 不计（宁少不多）"
    st = deliver_wire.note_opt_wire(st, "full", 509_000)
    assert st == ("full", 0, 509_000)
    st = deliver_wire.note_opt_wire(st, "delta", 154_000)
    assert st == ("delta", 355_000, 509_000)
    st = deliver_wire.note_opt_wire(st, "delta", 0)
    assert st == ("delta", 355_000, 509_000), "没带 opt（空）⇒ 形态与账本都不动"


def test_wire_row_keeps_per_game_only_when_asked(tmp_path: Path) -> None:
    """S6：补传体带逐局明细（默认仍摘——在线实时体若恢复，那条面照旧只带聚合列）。"""
    row = {"it": 3, "agg": {"kl": 0.01}, "perGame": [{"seed": 1}, {"seed": 2}], "perGameSecs": [0.5, 0.6]}
    thin, dropped = deliver_wire.wire_row(row)
    assert dropped == 2 and not ({"perGame", "perGameSecs"} & set(thin or {})), "默认摘"
    assert row["perGame"], "浅拷：原行一个键都不许丢"
    full, dropped2 = deliver_wire.wire_row(row, keep_per_game=True)
    assert dropped2 == 0 and full is row


def test_plan_run_wires_both_halves(tmp_path: Path) -> None:
    """S5 的**接线面**（无云机版）：`prepare_manifest` 与 `after_write` 真的挂上了。

    两半分开验过是不够的：这条链的失败模式就是「机制写好了、装配点没接」（缺基准不会报错，
    只是每轮都发整份）——所以这里从 `run_plan_job` 的入口跑一段真计划，再去看：
      ① `run_job` 收到的 manifest 逐轮对（锚点弹 `w_fmt`，其余 `delta-i16`）；
      ② 产物目录的 opt 逐轮对（锚点整份、其余容器）。
    """
    from common.manifest import normalize_manifest
    from common.protocol import encode_opt_tar
    from remote.plan_run import run_plan_job
    from tests.remote.test_run_loop import _iter_shaped_result, _prepare, _quiet

    plan, m, job_dir, first = _prepare(tmp_path, iters=4, start_it=1)
    # 起点那一轮（= 首轮，被播种进 it-001）给一份**真整份** opt：它是后续 delta 的基准。
    first = dict(
        first,
        opt_tar_b64=encode_opt_tar(_tar_bytes(_state(901), tmp_path, "seed")),
    )
    seen: list[tuple[int, str]] = []

    def fake(base_url: str, token: str, job: dict, **kw: object) -> dict:
        mm = normalize_manifest(job["manifest"])
        assert kw.get("preloaded") is not None
        seen.append((int(mm["it"]), str(mm.get("w_fmt") or "")))
        return _iter_shaped_result(
            mm,
            weights=json.dumps({"it": mm["it"]}).encode("utf-8"),
            opt=_tar_bytes(_state(900 + int(mm["it"])), tmp_path, f"job-{mm['it']}"),
            win=0.5,
        )

    run_plan_job(
        job_id=m["job_id"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        course=_Course(2),
        artifacts_dir=tmp_path / "art",
        run_job_fn=fake,
        log=_quiet,
    )

    # `_prepare` 的 `first_result` 已经是 it2（首位结果），所以这里只跑 it3/it4。
    assert seen == [(3, "delta-i16"), (4, "")], seen
    art = tmp_path / "art"
    forms = {
        it: delta_files.any_container_file(art / f"it-{it:03d}" / "opt.tar")
        for it in (1, 3, 4)
    }
    assert forms == {1: False, 3: True, 4: False}, forms


@pytest.mark.parametrize("it", [1, 3])
def test_metrics_row_shape_is_unchanged(tmp_path: Path, it: int) -> None:
    """账本行不许多出/少掉键（S4 的读物都在 `status()`/hub 账本，不在 `metrics.jsonl`）。"""
    root = tmp_path / "art"
    forms = RoundForms.for_store(root, plan={"start_it": 1}, course=_Course(2))
    row = _write_round(_store(root, forms), it, _state(800 + it), tmp_path)
    assert set(row) == {"it", "weights_fp", "opt_bytes", "ts"}

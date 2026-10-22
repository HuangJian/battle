"""tests/remote/test_pack_full.py —— 打包时把**整份**权重顶进去（`plan/offline-full-weights-backup`）。

离线盘那条腿的产物里 `it-NNN/weights.json` 装的是**回传用的 delta 容器**（`train_core` 的
`sent` 字节），而 hub 的接收侧会就地落成整份（`hub/store_offline` + `hub/deltas`）。打包出口
（`artifacts.zip` / 一键包）绕过接收侧 ⇒ 包里 232 份权重在本机一份都解不开。

本文件钉住三件事：

  1. `finalize(full_for=…)` / `write_deliver_zip(full_for=…)` 把**整份**顶成 `weights.json`、
     并把原件旁存成 `weights.delta`（与 hub 树逐字同形）；**产物根一个字节不改**。
  2. 不给 `full_for` 时逐字就是以前的行为（没有 `.delta` 旁件，`weights.json` 是原件）。
  3. 落地过的包导入时，`rows-fp` 与 `state-fp` 两项体检**不再假红**（它们记的是**原件**的 sha，
     而包里两版都在 ⇒ 「两态都认」才是正确判据）。

为什么 `full_for` 是**注入**而不是 import：`remote/artifacts` 与 `remote/offline_deliverable`
都是 L0（前者被 `hub.store_offline` L1 直接 import），import 落地器（L1）是向上边
（`tests/test_remote_dag.py`）。注入实现住在 L6 的 `plan_run._pack_full_for`。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.hashing import sha256_bytes
from common.protocol import OFFLINE_DELIVERED_NAME
from remote import land_provider, offline_boot, weights_delta
from remote.artifacts import ArtifactStore
from remote.deliver_zip import import_deliver_zip
from remote.offline_deliver import OfflineDeliverer
from remote.offline_deliverable import WEIGHTS_NAME, write_deliver_zip


def _weights(it: int) -> bytes:
    return json.dumps({"it": it, "w": it * 0.5}).encode("utf-8")


def _full_weights(it: int) -> bytes:
    """「整份」的替身：与原件**字节不同**（判据必须是「哪一份进了包」，不是「差不多」）。"""
    return json.dumps({"it": it, "w": it * 0.5, "full": True}).encode("utf-8")


def _store(tmp_path: Path, *, iters=(1, 2, 3)) -> ArtifactStore:
    root = tmp_path / "art"
    store = ArtifactStore(root, run_id="pack-full")
    store.start(
        {"start_it": iters[0] - 1, "end_it": iters[-1], "iters_total": iters[-1], "pairs": {}},
        {"runId": "pack-full", "commit": "c" * 40},
    )
    for it in iters:
        store.checkpoint(
            it,
            weights_json=_weights(it),
            opt_tar=b"opt-%d" % it,
            row={"agg": {"kl": 0.01}, "wall_sec": 1.0},
        )
    return store


def _full_for(tmp_path: Path, its: set[int]):
    """`full_for` 的替身：给点名的那几轮备一个「整份」文件。"""

    def full_for(root: Path, it: int) -> dict[str, Path]:
        if it not in its:
            return {}
        p = tmp_path / f"full-{it}.json"
        p.write_bytes(_full_weights(it))
        return {"weights.json": p}

    return full_for


def _zip_names(zp: Path) -> set[str]:
    with zipfile.ZipFile(zp) as z:
        return set(z.namelist())


def test_finalize_packs_the_full_weights_and_keeps_the_original_as_delta(tmp_path: Path) -> None:
    """W2：段末全量包里，被落地的那一轮 = 整份顶名 + 原件旁存；产物根一字未改。"""
    store = _store(tmp_path)
    before = {p: p.read_bytes() for p in (store.root / "it-002").iterdir() if p.is_file()}
    assert store.finalize(state="complete", full_for=_full_for(tmp_path, {2})) is not None
    zp = store.root / ArtifactStore.ALL_ZIP
    with zipfile.ZipFile(zp) as z:
        assert z.read("it-002/weights.json") == _full_weights(2), "整份没顶上去"
        assert z.read("it-002/weights.delta") == _weights(2), "原件必须旁存（hub 树同形）"
        assert z.read("it-001/weights.json") == _weights(1), "没被落地的那轮照旧是原件"
        assert {"it-001/weights.delta"}.isdisjoint(z.namelist()), "没落地就不带旁件"
    after = {p: p.read_bytes() for p in (store.root / "it-002").iterdir() if p.is_file()}
    assert after == before, "打包只读：产物根一个字节都不许改"


def test_finalize_without_full_for_is_the_old_behaviour(tmp_path: Path) -> None:
    """不注入 = 逐字旧行为（没有 `.delta` 旁件）——默认路径零变化。"""
    store = _store(tmp_path)
    assert store.finalize(state="complete") is not None
    names = _zip_names(store.root / ArtifactStore.ALL_ZIP)
    assert {"it-001/weights.delta", "it-002/weights.delta"}.isdisjoint(names)
    with zipfile.ZipFile(store.root / ArtifactStore.ALL_ZIP) as z:
        assert z.read("it-002/weights.json") == _weights(2)


def test_write_deliver_zip_uses_the_injected_full(tmp_path: Path) -> None:
    """W4：一键包走同一套判据（同一个 `full_for` 约定）。"""
    store = _store(tmp_path, iters=(1, 2))
    dest = tmp_path / "deliver-x.zip"
    ok = write_deliver_zip(
        store.root,
        dest,
        course="",
        source="pending",
        log=lambda _m: None,
        full_for=_full_for(tmp_path, {2}),
    )
    assert ok
    with zipfile.ZipFile(dest) as z:
        assert z.read("it-002/weights.json") == _full_weights(2)
        assert z.read("it-002/weights.delta") == _weights(2)
        assert z.read("it-001/weights.json") == _weights(1)


def _f32_weights(it: int) -> bytes:
    """一份**真**的整份权重 JSON（`params.<名>.{shape,data}`，f32 LE base64）——delta 容器的原料。"""
    import base64

    import numpy as np

    arr = np.array([it, it + 0.5, it - 1.5, it * 2], dtype="<f4").reshape(2, 2)
    return json.dumps(
        {
            "wver": 1,
            "params": {"w": {"shape": [2, 2], "data": base64.b64encode(arr.tobytes()).decode()}},
        },
        sort_keys=True,
    ).encode("utf-8")


def _delta_rounds(tmp_path: Path, *, n: int, base_dir: Path | None = None) -> tuple[ArtifactStore, bytes]:
    """造 `n` 轮：每一轮的 `weights.json` 都是**相对上一轮的 delta 容器**（与云机同形状）。

    返回 `(store, 起点整份)`；`base_dir` 给出时，把每轮用到的基准写进 `<base_dir>/<sha>`
    （= `blob_cache` 的内容寻址布局）。
    """
    root = tmp_path / "art"
    store = ArtifactStore(root, run_id="land", log=lambda _m: None)
    store.start(
        {"start_it": 0, "end_it": n, "iters_total": n, "pairs": {}},
        {"runId": "land", "commit": "c" * 40},
    )
    prev = _f32_weights(0)
    if base_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / sha256_bytes(prev)).write_bytes(prev)
    for it in range(1, n + 1):
        cur = _f32_weights(it)
        container = weights_delta.pack_weights_delta(prev, cur, base_sha=sha256_bytes(prev))
        store.checkpoint(it, weights_json=container, opt_tar=b"opt-%d" % it, row={"stage": 0})
        if base_dir is not None:
            (base_dir / sha256_bytes(cur)).write_bytes(cur)
        prev = cur
    return store, _f32_weights(0)


def test_provider_lands_a_real_delta_round_from_the_blob_cache(tmp_path: Path) -> None:
    """Q5 的核心：真 delta 容器 + `blob_cache` 里的基准 ⇒ 交回**整份**；产物根本体不动。"""
    cache = tmp_path / "blob_cache"
    store, _first = _delta_rounds(tmp_path, n=2, base_dir=cache)
    before = store.weights_path(2).read_bytes()
    assert weights_delta.is_container(before), "前置：产物里那一轮必须是 delta（云机的真实形状）"

    logs: list[str] = []
    got = land_provider.provider(blob_roots=[cache], log=logs.append)(store.root, 2)
    assert {"weights.json"} <= set(got), logs
    landed = Path(got["weights.json"]).read_bytes()
    assert not weights_delta.is_container(landed), "交回的必须是整份"
    # 逐字节等于「容器 + 基准」的重建结果（= 本仓唯一的落地口径）。
    assert landed == weights_delta.apply_weights_delta(_f32_weights(1), before)
    assert store.weights_path(2).read_bytes() == before, "落地是旁路缓存，产物根一个字节不改"


def test_provider_gives_up_after_three_missing_bases(tmp_path: Path) -> None:
    """基准全不在 ⇒ 不抛、不发整份，且**连续 3 轮就停试**（人点一下的 cell 不陪跑 N 轮扫读）。"""
    store, _ = _delta_rounds(tmp_path, n=6)
    logs: list[str] = []
    one = land_provider.provider(blob_roots=[], log=logs.append)
    for it in range(1, 7):
        assert {"weights.json"}.isdisjoint(one(store.root, it)), it
    warns = [m for m in logs if "weights.json 落地失败" in m]
    assert len(warns) == land_provider.MAX_FAILURES, logs
    assert sum(m.startswith("WARN 打包：weights.json 已连续失败") for m in logs) == 1, logs


def _nb_cfg(tmp_path: Path, local: Path, *, course: str = "c5-gae") -> dict:
    return {
        "course": course,
        "work_dir": str(tmp_path / "w"),
        "local_root": str(local),
        "download_dir": str(tmp_path / "out"),
    }


def test_notebook_one_click_packs_the_full_weights(tmp_path: Path) -> None:
    """Q5：notebook 那个「中途取回」cell 这条腿——真 delta 产物根 + blob_cache ⇒ 包内是整份。"""
    cache = tmp_path / "w" / "blob_cache"
    store, _ = _delta_rounds(tmp_path, n=3, base_dir=cache)
    before = store.weights_path(3).read_bytes()
    logs: list[str] = []
    made = offline_boot.package_pending_full(_nb_cfg(tmp_path, store.root), logs.append)
    assert [p.name for p in made] == ["deliver-c5-gae@land-it3.zip"], logs
    with zipfile.ZipFile(made[0]) as z:
        landed = z.read("it-003/weights.json")
        assert not weights_delta.is_container(landed), "一键包里必须是整份（换机器能直接评估）"
        assert landed == weights_delta.apply_weights_delta(_f32_weights(2), before)
        assert weights_delta.is_container(z.read("it-003/weights.delta")), "原件旁存（两版都在）"
        # 历史轮走 sidecar/缓存也应该是整份（不能在包里混一半）
        assert not weights_delta.is_container(z.read("it-001/weights.json"))
    assert store.weights_path(3).read_bytes() == before, "打包只读：产物根不改"


def test_notebook_pack_falls_back_loudly_without_the_provider_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """老 notebook（引导格没拉 `land_provider.py`）⇒ 包照出、权重是原件、一行说明。"""
    store, _ = _delta_rounds(tmp_path, n=1, base_dir=tmp_path / "w" / "blob_cache")
    logs: list[str] = []

    def _missing() -> object:
        raise ImportError("找不到 land_provider（notebook 引导格没拉它？）")

    monkeypatch.setattr(offline_boot, "_load_land_provider", _missing)
    made = offline_boot.package_pending_full(_nb_cfg(tmp_path, store.root), logs.append)
    assert [p.name for p in made] == ["deliver-c5-gae@land-it1.zip"], logs
    with zipfile.ZipFile(made[0]) as z:
        assert weights_delta.is_container(z.read("it-001/weights.json"))
    assert any(m.startswith("[pack] 找不到 land_provider") for m in logs), logs


def test_repacking_an_already_landed_round_keeps_names_unique(tmp_path: Path) -> None:
    """重导过的目录（整份 + 原件都在）再打一次包：包内名不重复、`.delta` 仍是原件。

    这一档很容易写错：`standard_weights_copy` 对「本来就是整份」的输入原样交回同一路径 ⇒
    若不加「alt 与源同一就照旧装 + 包内名去重」，`weights.delta` 会先拿到**整份**字节，
    再被真原件覆盖成两条同名头（zip 允许重复名，读方取哪条看工具）。
    """
    store = _store(tmp_path, iters=(1,))
    d = store.dir_for(1)
    original = (d / "weights.json").read_bytes()
    (d / "weights.delta").write_bytes(original)
    (d / "weights.json").write_bytes(_full_weights(1))

    def full_for(root: Path, it: int) -> dict[str, Path]:
        return {"weights.json": d / "weights.json"} if it == 1 else {}

    dest = tmp_path / "repack.zip"
    ok = write_deliver_zip(
        store.root, dest, course="", source="pending", log=lambda _m: None, full_for=full_for
    )
    assert ok
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), f"包内名重复：{names}"
        assert z.read("it-001/weights.json") == _full_weights(1)
        assert z.read("it-001/weights.delta") == original


def test_landed_package_passes_rows_fp_and_state_fp(tmp_path: Path) -> None:
    """P0-4：落地过的包导入时两项指纹体检都绿（记的是**原件**的 sha，包内两版都在）。"""
    store = _store(tmp_path)
    store.finalize(state="complete", full_for=_full_for(tmp_path, {1, 2, 3}))
    got = import_deliver_zip(store.root / ArtifactStore.ALL_ZIP, tmp_path / "deliver", course="")
    checks = {c["id"]: c for c in got["checks"]}
    assert checks["rows-fp"]["ok"], checks["rows-fp"]
    assert checks["state-fp"]["ok"], checks["state-fp"]
    # 落地件的确到位：末轮 ckpt 是整份，原件仍在旁边
    last = Path(got["ckpt"])
    assert last.read_bytes() == _full_weights(3)
    assert (last.parent / "weights.delta").read_bytes() == _weights(3)


def test_row_for_accepts_the_sibling_delta_after_landing(tmp_path: Path) -> None:
    """P0-4 的第三处读者：产物被落地后，账本行（记原件 sha）仍然配得上这一轮。"""
    store = _store(tmp_path, iters=(1,))
    d = store.dir_for(1)
    original = (d / "weights.json").read_bytes()
    (d / "weights.delta").write_bytes(original)
    (d / "weights.json").write_bytes(_full_weights(1))
    (store.root / OFFLINE_DELIVERED_NAME).write_text("{}", encoding="utf-8")

    deliv = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="sekret",
        run_id="pack-full",
        artifacts_dir=store.root,
        log=lambda _m: None,
    )
    row = deliv._row_for(1, sha256_bytes(_full_weights(1)))
    assert row is not None, "落地后（weights.json 是整份、原件在 .delta）行必须还能认出来"
    assert row["weights_fp"] == sha256_bytes(original)

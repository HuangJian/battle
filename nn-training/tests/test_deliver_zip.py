"""tests/test_deliver_zip.py —— 训练产物 zip（`deliver-<课程>.zip`）的导入。

控制台那条腿：导出任务包 → 云机跑完 → 把产物 zip 交回来 → 导入并自动按课程配置
跑 A 层评估。本文件钉住**导入这一侧**的契约（评估由控制台接着发起）：

  * 一份真产物 zip（`ArtifactStore` 亲手打的）导入后：轮次可被发现、ckpt 指向末轮
    权重、`state.json` 的 run_id 是权威；
  * **不可信输入的三个门**：zip-slip 拒收、拿错包（把任务包传上来）要明确指出、
    文件名课程与控制台课程不一致要拒收（A 课权重评 B 课语料是最贵的错）；
  * 幂等：重导同一个 run 不叠加目录、不留 `*.tmp-*` 壳（半截目录会让「按目录名认
    run」的读方看到两份）。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.artifacts import ArtifactStore
from remote.bundle import BUNDLE_INDEX
from remote.deliver_zip import (
    IMPORT_JSON_MARK,
    course_from_filename,
    import_deliver_zip,
    main,
)
from remote.protocol import ProtocolError


def _weights(it: int) -> bytes:
    return json.dumps({"it": it, "w": it * 0.5}).encode("utf-8")


def _make_artifact_zip(tmp_path: Path, *, iters=(1, 2, 3), run_id="x1-demo", state="complete") -> Path:
    """用 `ArtifactStore` 亲手打一份真产物 zip（形状与云机产出的逐字段一致）。"""
    root = tmp_path / "art-src"
    store = ArtifactStore(root, run_id=run_id)
    store.start(
        {"start_it": iters[0] - 1, "end_it": iters[-1], "iters_total": iters[-1], "pairs": {}},
        {"runId": run_id, "commit": "c" * 40},
    )
    for it in iters:
        store.checkpoint(
            it,
            weights_json=_weights(it),
            opt_tar=b"opt-%d" % it,
            row={"agg": {"kl": 0.01}, "wall_sec": 1.0},
        )
    store.finalize(state=state, summary={"last_it": iters[-1]})
    out = tmp_path / "deliver-demo.zip"
    out.write_bytes((root / ArtifactStore.ALL_ZIP).read_bytes())
    return out


def test_import_discovers_iters_and_points_at_the_last_ckpt(tmp_path: Path) -> None:
    z = _make_artifact_zip(tmp_path, iters=(1, 2, 3))
    got = import_deliver_zip(z, tmp_path / "deliver", course="")
    assert got["run_id"] == "x1-demo"
    assert got["iters"] == [1, 2, 3] and got["last_it"] == 3
    assert Path(got["ckpt"]).read_bytes() == _weights(3)
    assert got["state"] == "complete"
    dest = Path(got["dir"])
    assert dest.name == "x1-demo"
    # 落地目录是一份可直接读的产物（计划/manifest/账本都在）
    assert (dest / "plan.json").exists() and (dest / "manifest.json").exists()
    assert (dest / "metrics.jsonl").exists()
    assert not list(dest.parent.glob("*.tmp-*"))  # 不留半截壳


def test_reimport_same_run_does_not_stack_directories(tmp_path: Path) -> None:
    """重导同一个 run（云机重跑一遍再交回来）⇒ 目录被替换，不是并列两份。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2))
    dest = tmp_path / "deliver"
    first = import_deliver_zip(z, dest)
    (Path(first["dir"]) / "STALE.txt").write_text("旧的一份", encoding="utf-8")
    z2 = _make_artifact_zip(tmp_path, iters=(1, 2, 3, 4))
    second = import_deliver_zip(z2, dest)
    assert second["dir"] == first["dir"]
    assert second["iters"] == [1, 2, 3, 4]
    assert not (Path(second["dir"]) / "STALE.txt").exists()
    assert [p.name for p in dest.iterdir()] == ["x1-demo"]


def test_bundle_zip_is_rejected_with_a_pointed_message(tmp_path: Path) -> None:
    """把任务包当产物传上来（最可能的一次手滑）：错误信息要直接说清该传什么。"""
    task = tmp_path / "task-x1.zip"
    with zipfile.ZipFile(task, "w") as z:
        z.writestr(BUNDLE_INDEX, json.dumps({"magic": "battle2-task-bundle", "proto": 1}))
        z.writestr("plan.json", "{}")
    with pytest.raises(ProtocolError) as ei:
        import_deliver_zip(task, tmp_path / "deliver")
    assert "任务包" in str(ei.value) and "run_loop" in str(ei.value)
    assert not list((tmp_path / "deliver").glob("*.tmp-*"))


def test_zip_without_weights_is_rejected(tmp_path: Path) -> None:
    z = tmp_path / "deliver-x1.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("README.txt", "not an artifact")
        zf.writestr("it-001/row.json", "{}")  # 有目录没权重——仍然不是产物
    with pytest.raises(ProtocolError) as ei:
        import_deliver_zip(z, tmp_path / "deliver")
    assert "weights.json" in str(ei.value)


def test_zip_slip_member_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    """包的来源是「人搬来的文件」——越界成员必须在解压前拒掉（与任务包同一道门）。"""
    z = tmp_path / "deliver-evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("it-001/weights.json", "{}")
        zf.writestr("../../evil.txt", "pwned")
    with pytest.raises(ProtocolError):
        import_deliver_zip(z, tmp_path / "deliver")
    assert not (tmp_path.parent / "evil.txt").exists()
    assert not list((tmp_path / "deliver").glob("*.tmp-*"))


def test_course_mismatch_in_filename_is_rejected(tmp_path: Path) -> None:
    """`deliver-<课程>.zip` 的名字与控制台当前课程不一致 ⇒ 拒收（否则读数会静默错位）。"""
    src = _make_artifact_zip(tmp_path, iters=(1,))
    z = tmp_path / "deliver-x9-other.zip"
    z.write_bytes(src.read_bytes())
    with pytest.raises(ProtocolError) as ei:
        import_deliver_zip(z, tmp_path / "deliver", course="x1-rebirth-a2")
    assert "x9-other" in str(ei.value) and "x1-rebirth-a2" in str(ei.value)


def test_course_from_filename_only_accepts_the_convention() -> None:
    assert course_from_filename("deliver-x1-rebirth-a2.zip") == "x1-rebirth-a2"
    assert course_from_filename("DELIVER-C4.zip") == "C4"
    for odd in ("x1-rebirth-a2.zip", "deliver-x1.tar.gz", "deliver-.zip", "deliver-a/b.zip"):
        assert course_from_filename(odd) == ""


def test_state_run_id_wins_over_filename(tmp_path: Path) -> None:
    """`state.json` 的 run_id 才是权威（目录名按文件名猜的那份只是初见）——否则同一个\n    run 会因为文件名不同而分裂成两个目录。"""
    src = _make_artifact_zip(tmp_path, iters=(1, 2), run_id="real-run-id")
    z = tmp_path / "deliver-x1-demo.zip"
    z.write_bytes(src.read_bytes())
    got = import_deliver_zip(z, tmp_path / "deliver", course="x1-demo")
    assert got["run_id"] == "real-run-id"
    assert Path(got["dir"]).name == "real-run-id"


def test_nested_zip_is_unwrapped(tmp_path: Path) -> None:
    """用户把目录整包打一层（`unzip -d` / 拖文件夹）也要能导——下探到 `it-*/` 那层。"""
    inner = _make_artifact_zip(tmp_path, iters=(1, 2))
    nested = tmp_path / "deliver-nested.zip"
    with zipfile.ZipFile(nested, "w") as zf, zipfile.ZipFile(inner) as src:
        for name in src.namelist():
            zf.writestr(f"x1-demo/{name}", src.read(name))
    got = import_deliver_zip(nested, tmp_path / "deliver")
    assert got["iters"] == [1, 2]
    assert (Path(got["dir"]) / "it-002" / "weights.json").exists()
    assert not list(Path(got["dir"]).parent.glob("*.tmp-*"))


def test_cli_prints_machine_readable_result(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    z = _make_artifact_zip(tmp_path, iters=(1, 2))
    rc = main(["--zip", str(z), "--dest", str(tmp_path / "deliver"), "--json-only"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith(IMPORT_JSON_MARK)
    payload = json.loads(out[-1][len(IMPORT_JSON_MARK) :])
    assert payload["last_it"] == 2 and Path(payload["ckpt"]).exists()


def test_cli_returns_nonzero_on_bad_zip(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    bad = tmp_path / "deliver-broken.zip"
    bad.write_bytes(b"not a zip at all")
    rc = main(["--zip", str(bad), "--dest", str(tmp_path / "deliver")])
    assert rc == 2
    assert "导入失败" in capsys.readouterr().err

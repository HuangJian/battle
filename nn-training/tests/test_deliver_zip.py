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


def _make_artifact_zip(
    tmp_path: Path,
    *,
    iters=(1, 2, 3),
    run_id="x1-demo",
    state="complete",
    row: dict | None = None,
    eval_rows: list[dict] | None = None,
) -> Path:
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
            row=row or {"agg": {"kl": 0.01}, "wall_sec": 1.0},
        )
    if eval_rows is not None:
        (root / ArtifactStore.EVAL_LOG_NAME).write_text(
            "\n".join(json.dumps(r) for r in eval_rows) + "\n", encoding="utf-8"
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


# ────────────────── 导入 ⇒ 课程账本（控制台「各轮指标表」的唯一数据源） ──────────────────


def test_imported_rounds_land_in_the_course_ledger(tmp_path: Path) -> None:
    """导入的逐轮训练行要并进 `tmp/<课程>/training_log.jsonl`（幂等）。

    用户 2026-09-22 实测缺口：导入后控制台**各轮指标表一行都不显示**——因为那张表只读
    课程账本的 `iteration` 事件（`dashboard/src/server/api/state-view.ts`），而导入只落
    了产物目录（权重/优化器/`metrics.jsonl`）。
    """
    z = _make_artifact_zip(tmp_path, iters=(1, 2), run_id="r1")
    dest = tmp_path / "demo" / "deliver"
    log: list[str] = []
    got = import_deliver_zip(z, dest, course="demo", log=log.append)
    assert got["metric_rows"] == 2
    ledger = dest.parent / "training_log.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["iter"] for r in rows] == [1, 2]
    assert all(r["event"] == "iteration" for r in rows)
    # 来源标记由**共享翻译表**（`remote.artifacts.ledger_row_from_metrics`）打：`run_id` 指包，
    # `source` 指谁搬的（导入 `deliver_import` / 实时补传 `offline_backfeed`）——两条腿同键名，
    # 复盘时能一眼分辨曲线从哪来。
    assert all(r["run_id"] == "r1" and r["source"] == "deliver_import" for r in rows)
    assert rows[0]["kl"] == 0.01 and rows[0]["time"], "agg/time 要按账本口径搬运"
    assert any("课程账本" in m for m in log), log
    # 幂等：同一个包再导一次不写第二遍（账本是多写者文件，重导不应加倍）
    got2 = import_deliver_zip(z, dest, course="demo", log=log.append)
    assert got2["metric_rows"] == 0
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_imported_cloud_eval_summary_reaches_the_course_ledger(tmp_path: Path) -> None:
    """云机 A 层评估（逐局行 **+ summary**）导入后都要落进课程账本。

    用户 2026-09-23 实测：`x20-demo-mix` 云腿 it50–110、每 5 轮 400 局、`node=cloud` 的
    读数全在账本里，控制台却一栏不显示——两条腿合并都只并 `event:"eval"` 逐局行，
    把 summary 丢了，而控制台 eval 列 / eval 弹窗 / 开课回执与门判据**只读 summary**
    （`readEvalSummaries` 只在 `event == "eval_summary"` 时建条目）。
    """
    z = _make_artifact_zip(
        tmp_path,
        iters=(1, 2),
        run_id="r3",
        eval_rows=[
            {"event": "eval", "iter": 2, "wver": "a" * 16, "stage": 0, "seed": 1, "node": "cloud"},
            {
                "event": "eval_summary",
                "iter": 2,
                "wver": "a" * 16,
                "games": 400,
                "wins": 44,
                "winRate": 0.11,
                "nodes": {"cloud": 400},
            },
        ],
    )
    dest = tmp_path / "demo" / "deliver"
    log: list[str] = []
    got = import_deliver_zip(z, dest, course="demo", log=log.append)
    assert got["eval_rows"] == 1 and got["eval_summaries"] == 1
    ledger = dest.parent / "eval_log.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["event"] for r in rows] == ["eval", "eval_summary"]
    assert rows[1]["games"] == 400 and rows[1]["nodes"] == {"cloud": 400}
    assert any("summary" in m for m in log), log
    # 幂等：同一个包再导一次，两类都不再写
    got2 = import_deliver_zip(z, dest, course="demo", log=log.append)
    assert (got2["eval_rows"], got2["eval_summaries"]) == (0, 0)
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_imported_ledger_row_maps_report_and_agg(tmp_path: Path) -> None:
    """字段搬运：report/agg → 账本字段名（控制台读的就是这些键）。"""
    z = _make_artifact_zip(
        tmp_path,
        iters=(4,),
        run_id="r2",
        row={
            "agg": {
                "kl": 0.02,
                "entropy": 0.5,
                "policy": 0.1,
                "value": 0.3,
                "mean_ret": 7.0,
                # 缰绳/demo 遥测：产物行的 agg 里**一直有**，2026-09-23 才进搬运表
                # （用户发现「demo_bc 全缺」——不是没跑，是记丢了）。
                "kickstart": 0.02,
                "demo_bc": 1.45,
            },
            "report": {
                "games": 328,
                "shards": 328,
                "winRate": 0.11,
                "totalSamples": 76800,
                "totalTicks": 123456,
                "elapsedSec": 650.7,
                "outcomes": {"loss": 300},
                # 逐维度/分数统计（2026-09-22 起产物行带）：控制台 kills/accuracy/loot 与
                # score 列的数据源——旧包没这两块时对应列留空（下面另有一条用例钉它）。
                "dimMeans": {"progress": 0.4, "accuracy": 0.12, "loot": 0.3},
                "scoreStats": {"mean": 0.87, "std": 0.05},
            },
            "wall_sec": 94.5,
            "ppo_sec": 88.9,
            "rollout_sec": 4.6,
            "steps": 48000,
            "chunks": 47,
        },
    )
    got = import_deliver_zip(z, tmp_path / "demo" / "deliver", course="demo", log=lambda _m: None)
    assert got["last_it"] == 4
    line = (tmp_path / "demo" / "training_log.jsonl").read_text(encoding="utf-8").strip()
    ev = json.loads(line)
    assert ev["iter"] == 4 and ev["event"] == "iteration"
    assert ev["winRate"] == 0.11 and ev["samples"] == 76800 and ev["ticks"] == 123456
    assert ev["rollout_sec"] == 4.6 and ev["ppo_sec"] == 88.9
    assert ev["policy"] == 0.1 and ev["value"] == 0.3 and ev["kl"] == 0.02
    assert ev["kickstart"] == 0.02 and ev["demo_bc"] == 1.45, "缰绳/demo 遥测要搬进账本"
    assert ev["outcomes"] == {"loss": 300} and ev["steps"] == 48000
    # 控制台用 ticks/expectedGames 算平均每局时长 ⇒ 映射了 report.games 这一列才不是 0
    assert ev["expectedGames"] == 328 and ev["ticks"] // ev["expectedGames"] == 376
    # 逐维度/分数：分层路径要翻译成平铺键（kills = dim_means.progress × 20）
    assert ev["dim_means"] == {"progress": 0.4, "accuracy": 0.12, "loot": 0.3}
    assert ev["score_mean"] == 0.87 and ev["score_std"] == 0.05


def test_old_package_without_dims_leaves_those_columns_empty(tmp_path: Path) -> None:
    """旧包（产物行没有 dimMeans/scoreStats）⇒ 那几列**留空**，不写 0。

    写 0 在表上会被读成「真的零击杀/零命中」——缺数据与零是两件事，宁可空。
    """
    z = _make_artifact_zip(
        tmp_path,
        iters=(7,),
        run_id="r7",
        row={"agg": {"kl": 0.02}, "report": {"games": 328, "winRate": 0.1}, "wall_sec": 1.0},
    )
    import_deliver_zip(z, tmp_path / "demo" / "deliver", course="demo", log=lambda _m: None)
    ev = json.loads(
        (tmp_path / "demo" / "training_log.jsonl").read_text(encoding="utf-8").strip()
    )
    assert "dim_means" not in ev and "score_mean" not in ev and "score_std" not in ev
    # 旧包/未配 demo 的轮没有这两个键 ⇒ 账本里也**不下落成 0**（与上面同一口径）。
    assert "kickstart" not in ev and "demo_bc" not in ev
    assert ev["winRate"] == 0.1, "能搬的照搬"
    # 起点快照（it0）不是一轮：不写 iteration 行（控制台会把它当成轮次）
    assert "dim_means" not in ev, "产物行里没有的东西不许编（那张表的列宁可为空）"


def test_ledger_merge_skips_it0_and_bad_rows(tmp_path: Path) -> None:
    """it0/坏行不进账本：只搬真正的一轮训练行。"""
    root = tmp_path / "art-src"
    store = ArtifactStore(root, run_id="r3")
    store.start(
        {"start_it": 0, "end_it": 1, "iters_total": 1, "pairs": {}},
        {"runId": "r3", "commit": "c" * 40},
    )
    store.checkpoint(0, weights_json=_weights(0), opt_tar=b"", row=None)  # 起点快照
    store.checkpoint(1, weights_json=_weights(1), opt_tar=b"o", row={"agg": {"kl": 0.0}})
    store.finalize(state="complete", summary={"last_it": 1})
    z = tmp_path / "deliver-demo.zip"
    z.write_bytes((root / ArtifactStore.ALL_ZIP).read_bytes())
    got = import_deliver_zip(z, tmp_path / "demo" / "deliver", course="demo", log=lambda _m: None)
    assert got["metric_rows"] == 1, "it0 不进账本（它不是一轮训练）"
    ledger = tmp_path / "demo" / "training_log.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["iter"] for r in rows] == [1]


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

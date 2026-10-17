"""tests/remote/test_deliver_zip.py —— 训练产物 zip（`deliver-<课程>.zip`）的导入。

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
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError
from remote.artifacts import ArtifactStore
from remote.bundle import BUNDLE_INDEX
from remote.deliver_zip import (
    IMPORT_JSON_MARK,
    course_from_filename,
    import_deliver_zip,
    import_from_run,
    main,
    write_run_complete_for_run,
)


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
    salt: bytes = b"",
    tag: str = "demo",
) -> Path:
    """用 `ArtifactStore` 亲手打一份真产物 zip（形状与云机产出的逐字段一致）。

    `salt` / `tag`：同一个 run 的**第二份**包要用不同的 tag（不会与第一份撞落点/文件名），
    salt 用来造「同名轮但字节不同」的那一档（D5 的先到者为准）。
    """
    root = tmp_path / f"art-src-{tag}"
    store = ArtifactStore(root, run_id=run_id)
    store.start(
        {"start_it": iters[0] - 1, "end_it": iters[-1], "iters_total": iters[-1], "pairs": {}},
        {"runId": run_id, "commit": "c" * 40},
    )
    for it in iters:
        store.checkpoint(
            it,
            weights_json=_weights(it) + salt,
            opt_tar=b"opt-%d" % it,
            row=row or {"agg": {"kl": 0.01}, "wall_sec": 1.0},
        )
    if eval_rows is not None:
        (root / ArtifactStore.EVAL_LOG_NAME).write_text(
            "\n".join(json.dumps(r) for r in eval_rows) + "\n", encoding="utf-8"
        )
    store.finalize(state=state, summary={"last_it": iters[-1]})
    out = tmp_path / f"deliver-{tag}.zip"
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


def test_reimport_same_run_merges_into_one_directory(tmp_path: Path) -> None:
    """重导同一个 run（云机重跑一遍再交回来）⇒ 逐轮并进**同一个**目录，不是并列两份。

    新语义（plan §8.2-1 / D5）：已有的轮**不重写**（先到者为准），只补缺的轮与缺的件。
    """
    z = _make_artifact_zip(tmp_path, iters=(1, 2))
    dest = tmp_path / "deliver"
    first = import_deliver_zip(z, dest)
    z2 = _make_artifact_zip(tmp_path, iters=(1, 2, 3, 4), tag="second")
    second = import_deliver_zip(z2, dest)
    assert second["dir"] == first["dir"]
    assert second["iters"] == [1, 2, 3, 4]
    assert [p.name for p in dest.iterdir()] == ["x1-demo"]
    # 零冲突 ∧ 新包自述更靠后 ⇒ state.json 采纳新的（自述与盘上事实一致）
    st = json.loads((Path(second["dir"]) / "state.json").read_text(encoding="utf-8"))
    assert st["last_it"] == 4


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


# ────────────────── T6：end_it_reached 转交（run_complete 只由 python 写） ──────────────────


def test_import_alone_never_writes_run_complete(tmp_path: Path) -> None:
    """半段导入不得亮横幅（T6 负向）：导入器自己**永远**不落 `run_complete`。

    转交必须由控制台在「`run_id` 与 hub 段末摘要匹配 ∧ `end_it_reached`」之后显式发起
    （第二趟 `--end-it-reached`）——python 在没有那次显式授权时一行都不写。
    """
    z = _make_artifact_zip(tmp_path, iters=(1, 2), run_id="r-half")
    dest = tmp_path / "demo" / "deliver"
    import_deliver_zip(z, dest, course="demo", log=lambda _m: None)
    ledger = dest.parent / "training_log.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows and all(r["event"] == "iteration" for r in rows)


def test_transfer_writes_run_complete_from_product_facts(tmp_path: Path) -> None:
    """转交落 `run_complete`：`it` = 产物末轮、`iters` = 计划终点（两者都只从盘上事实取）。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2, 3), run_id="r-done")
    dest = tmp_path / "demo" / "deliver"
    import_deliver_zip(z, dest, course="demo", log=lambda _m: None)
    got = write_run_complete_for_run(dest, "r-done", log=lambda _m: None)
    assert got == {"run_id": "r-done", "it": 3, "iters": 3, "written": True}
    ledger = dest.parent / "training_log.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tail = rows[-1]
    assert tail["event"] == "run_complete" and tail["iter"] == 3 and tail["iters"] == 3
    assert "跑满" in tail["reason"] and len(tail["time"]) == 19
    # 幂等：重导/重转交不叠行（账本是多写者文件，重复行会让横幅读面之外的地方惊讶）
    again = write_run_complete_for_run(dest, "r-done", log=lambda _m: None)
    assert again["written"] is False
    rows2 = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows2) == len(rows)


def test_transfer_rejects_unknown_run_and_writes_nothing(tmp_path: Path) -> None:
    """`run_id` 不匹配（没有对应产物目录）⇒ 响亮拒绝，账本里不出现 `run_complete`。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2), run_id="r-x")
    dest = tmp_path / "demo" / "deliver"
    import_deliver_zip(z, dest, course="demo", log=lambda _m: None)
    with pytest.raises(ProtocolError) as ei:
        write_run_complete_for_run(dest, "no-such-run", log=lambda _m: None)
    assert "no-such-run" in str(ei.value)
    ledger = dest.parent / "training_log.jsonl"
    assert "run_complete" not in ledger.read_text(encoding="utf-8")


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


# ──────────────────── B0 回灌腿：hub 已收的 run 目录 → 本机账本 ────────────────────
#
# 为什么单独成组（plan/offline-open-ended-iters-and-loud-export §3.2.1 定案①）：
# 「无限轮」的下一段包只能从**本机账本**起（`plan.start_it = 本机 last_it`），而云机跑完
# 那一段时控制台不在场（产物在 hub 手里）—— 这条腿就是那唯一一条「账本前进一步」的通道。


def _hub_received_run(tmp_path: Path, *, run_id: str = "seg-1", iters=(1, 2, 3)) -> Path:
    """造一份**hub 已收**的 run 目录（与 hub 落地布局逐字段同形状）。"""
    root = tmp_path / "remote-jobs" / "offline" / run_id
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
            row={"agg": {"kl": 0.02}, "wall_sec": 1.0},
        )
    store.finalize(state="complete", summary={"last_it": iters[-1]})
    return root


def _ledger_iters(ledger: Path) -> list[int]:
    out: list[int] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if isinstance(ev, dict) and ev.get("event") == "iteration":
            out.append(int(ev["iter"]))
    return out


def test_import_from_run_backfills_the_local_ledger_to_the_segment_end(tmp_path: Path) -> None:
    """回灌：取件 + 账本推进到段末（DoD-3a 的可自动判据）+ 第二遍幂等。"""
    src = _hub_received_run(tmp_path)
    dest = tmp_path / "deliver"
    got = import_from_run(src, dest, course="c1", run_id="seg-1")
    assert got["imported"] is True
    assert got["iters"] == [1, 2, 3] and got["last_it"] == 3
    # 取件：轮次目录落进导入根（后续读方与人工导入同一形状）
    assert (dest / "seg-1" / "it-003" / "weights.json").read_bytes() == _weights(3)
    assert Path(got["ckpt"]).exists()
    # 账本推进到段末（下一段包的 `start_it` 就从这里起）
    assert max(_ledger_iters(tmp_path / "training_log.jsonl")) == 3
    # 幂等（plan §3.2.1 的「必写」）：第二遍直接说无需回灌，不重复合并
    again = import_from_run(src, dest, course="c1", run_id="seg-1")
    assert again["imported"] is False
    assert again["reason"]  # 说清为什么不用导（给人看的一行）
    assert max(_ledger_iters(tmp_path / "training_log.jsonl")) == 3  # 没多写一行


def test_import_from_run_never_rewrites_an_existing_round(tmp_path: Path) -> None:
    """轮次是**不可变快照**：已存在的那一份一律不碰（重写只会把已收的那份换掉）。"""
    src = _hub_received_run(tmp_path, iters=(1, 2))
    dest = tmp_path / "deliver"
    import_from_run(src, dest, run_id="seg-1")
    keep = dest / "seg-1" / "it-002" / "weights.json"
    keep.write_bytes(b"kept")
    (src / "it-002" / "weights.json").write_bytes(b"other")  # 源里换了字节
    import_from_run(src, dest, run_id="seg-1")
    assert keep.read_bytes() == b"kept"


def test_a_backup_drawer_is_importable_with_its_ledgers(tmp_path: Path) -> None:
    """DoD-2b（plan §3.3）：把**抽屉**当产物根导入 ⇒ 曲线读数与 eval 行都能并入课程账本。

    现场（报告 §3.1）：抽屉里只有 `weights`/`opt` ⇒ 权重能救、**读数救不回**（导入后曲线与
    eval 全空，得靠人从 `/tmp` 抢救）。这里从「真補传器写出来的抽屉」出发跑到导入，
    钉住整条链：抽屉 → `import_from_run` → 课程账本（`iteration` 行 + eval 逐局行）。
    """
    from remote.offline_deliver import OfflineDeliverer
    from worker.eval_rows import eval_row

    src = _hub_received_run(tmp_path, run_id="seg-1", iters=(1, 2))
    rows = [
        eval_row({"dims": {}, "win": True, "score": 90}, it=2, key16="k" * 16, task=(1, 200 + i), node="cloud")
        for i in range(2)
    ]
    (src / "eval_log.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    drawer = tmp_path / "net" / "backup"
    d = OfflineDeliverer(
        base_url="",  # 推不出去 ⇒ 两轮全 pending ⇒ 都进抽屉
        token="",
        run_id="seg-1",
        artifacts_dir=src,
        backup_root=drawer,
        log=lambda _m: None,
    )
    assert d.backup_pending() == 2
    assert (drawer / "metrics.jsonl").is_file() and (drawer / "eval_log.jsonl").is_file()
    dest = tmp_path / "deliver"
    got = import_from_run(drawer, dest, course="c1", run_id="seg-1")
    assert got["imported"] is True and got["iters"] == [1, 2]
    assert got["metric_rows"] == 2, "逐轮读数得随抽屉回来（控制台的各轮指标表只读课程账本）"
    assert got["eval_rows"] == 2, "eval 逐局行同理（门判据只认课程账本）"
    assert _ledger_iters(tmp_path / "training_log.jsonl") == [1, 2]
    ev = (tmp_path / "eval_log.jsonl").read_text(encoding="utf-8")
    assert ev.count('"event": "eval"') == 2


def test_import_from_run_rejects_a_directory_without_rounds(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-run"
    empty.mkdir(parents=True)
    with pytest.raises(ProtocolError):
        import_from_run(empty, tmp_path / "deliver", run_id="seg-1")
    # 半截目录不留：拒收之后导入根里不该出现这个 run（读方按目录名认 run）
    assert not (tmp_path / "deliver" / "seg-1").exists()


def test_from_run_cli_prints_the_import_handle(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """控制台那条路的 argv 形状：`--from-run <hub 已收目录> --dest … --course … --run-id …`。"""
    src = _hub_received_run(tmp_path, iters=(4, 5))
    rc = main(
        [
            "--from-run",
            str(src),
            "--dest",
            str(tmp_path / "deliver"),
            "--course",
            "c1",
            "--run-id",
            "seg-9",
            "--json-only",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out[len(IMPORT_JSON_MARK) :])
    assert payload["imported"] is True and payload["run_id"] == "seg-9"
    assert payload["last_it"] == 5 and (tmp_path / "deliver" / "seg-9").is_dir()


def test_course_from_filename_only_accepts_the_convention() -> None:
    assert course_from_filename("deliver-x1-rebirth-a2.zip") == "x1-rebirth-a2"
    assert course_from_filename("DELIVER-C4.zip") == "C4"
    for odd in ("x1-rebirth-a2.zip", "deliver-x1.tar.gz", "deliver-.zip", "deliver-a/b.zip"):
        assert course_from_filename(odd) == ""


def test_course_from_filename_ignores_the_at_annotation() -> None:
    """导出/一键包的历史名（`deliver-<课>@<run>-it<N>.zip`，§5.2）⇒ 课程段取 `@` **之前**。

    规则与控制台 `validateDeliverUpload` 必须一致（两边同改）：把整个 `@…` 尾巴当课程的话，
    一份本来正确的包会被判成「别的课的产物」（现场就是这个文件名格式被用来留历史）。
    """
    assert course_from_filename("deliver-x1-rebirth-a2@seg-1-it40.zip") == "x1-rebirth-a2"
    assert course_from_filename("deliver-C4@r3-it7.zip") == "C4"
    assert course_from_filename("deliver-@seg1.zip") == ""  # 课程段空 ⇒ 不猜


def _write_meta(z: zipfile.ZipFile, **over: object) -> None:
    meta = {
        "magic": "battle2-deliver-meta",
        "course": "x1-demo",
        "run_id": "run-meta-1",
        "it_from": 1,
        "it_to": 2,
        "generated_at": "2026-10-17T00:00:00+00:00",
        "source": "pending",
    }
    meta.update(over)
    z.writestr("IMPORT_META.json", json.dumps(meta))


def test_import_reads_the_package_meta_and_reports_it(tmp_path: Path) -> None:
    """包内自述（§5.2/§6.2-2）：一键包带的 `IMPORT_META.json` 要读得出来、上得了屏。

    另外它还是 `state.json` 缺/没 run_id 时**次优**的 run 名来源（比按文件名猜准确）。
    """
    src = _make_artifact_zip(tmp_path, iters=(1, 2))
    z = tmp_path / "deliver-x1-demo.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in zipfile.ZipFile(src).namelist():
            zf.writestr(n, zipfile.ZipFile(src).read(n))
        _write_meta(zf)
    got = import_deliver_zip(z, tmp_path / "deliver", course="x1-demo")
    assert (got["package_course"], got["package_source"]) == ("x1-demo", "pending")
    by = {c["id"]: c for c in got["checks"]}
    # 判据按**结构化字段**（DECISIONS 定案三的口径：控制台/用例按数据判，不挂措辞）：
    # 「这一包是待回传那个源」就是 `package_source` 这个读数字，不是那句 detail 的措辞。
    assert by["meta"]["ok"] is True and by["meta"]["package_course"] == "x1-demo"
    assert by["meta"]["package_source"] == "pending"


def test_import_rejects_a_package_whose_meta_names_another_course(tmp_path: Path) -> None:
    """★ 门④（§6.2-2-③）：包自述的课 ≠ 本次导入的课 ⇒ **拒收、不猜**（改名包）。

    这条与「文件名对账」互补：文件名可以改对，包内自述改不了（它是打包那一刻写的）。
    """
    src = _make_artifact_zip(tmp_path, iters=(1, 2))
    z = tmp_path / "deliver-x1-demo.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in zipfile.ZipFile(src).namelist():
            zf.writestr(n, zipfile.ZipFile(src).read(n))
        _write_meta(zf, course="x9-other")
    with pytest.raises(ProtocolError) as ei:
        import_deliver_zip(z, tmp_path / "deliver", course="x1-demo")
    msg = str(ei.value)
    assert "x9-other" in msg and "x1-demo" in msg, msg
    assert "切到 x9-other 再导" in msg, "要给动作而不只是报错"
    # 拒收 = **不落位**（`dest_root` 这一级是既有的、任何一道门之前就建的空目录）
    assert list((tmp_path / "deliver").iterdir()) == [], "拒收 ⇒ 一个轮次目录都不建"


def test_import_ignores_an_unknown_meta_magic_but_says_so(tmp_path: Path) -> None:
    """magic 不认识 ⇒ **当它不存在**（不拿一份来路不明的自述去对账），但体检里看得见。"""
    src = _make_artifact_zip(tmp_path, iters=(1, 2))
    z = tmp_path / "deliver-x1-demo.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in zipfile.ZipFile(src).namelist():
            zf.writestr(n, zipfile.ZipFile(src).read(n))
        _write_meta(zf, magic="something-else", course="x9-other")
    got = import_deliver_zip(z, tmp_path / "deliver", course="x1-demo")  # 不拒收
    assert got["package_course"] == ""
    by = {c["id"]: c for c in got["checks"]}
    # 结构化判据：「当它不存在」= 体检行不过且两段读数字都空（那句 detail 只是它的说法）。
    assert by["meta"]["ok"] is False
    assert (by["meta"]["package_course"], by["meta"]["package_source"]) == ("", "")


def test_import_reports_a_green_checklist(tmp_path: Path) -> None:
    """DoD-5c：成功路径上也有体检报告（全绿 + 轮次数 + 末轮 + 是否半段）。"""
    src = _make_artifact_zip(tmp_path, iters=(1, 2, 3))
    got = import_deliver_zip(src, tmp_path / "deliver")
    assert got["checks_ok"] is True and got["partial"] is False
    assert [c["id"] for c in got["checks"]] == [
        "entries",
        "shape",
        "continuity",
        "rows-fp",
        "weights",
        "state",
        "state-fp",
        "anchors",
        "body-limit",
        "meta",
    ]
    by = {c["id"]: c for c in got["checks"]}
    assert by["shape"]["detail"] == "it1 → it3（3 轮）"
    assert by["continuity"]["missing"] == []
    assert by["rows-fp"]["no_rows"] == [] and by["rows-fp"]["bad_fp"] == []
    assert by["state"]["state_last_it"] == 3 and by["state"]["package_last_it"] == 3
    assert by["anchors"]["missing"] == []
    # `IMPORT_META.json`：手工/老包没有它 ⇒ 这一项**不算不过**（只是没有那条证据）
    assert by["meta"]["ok"] is True and by["meta"]["package_course"] == ""


def test_import_checklist_reports_breaks_without_blocking_the_import(tmp_path: Path) -> None:
    """体检是**报告不是门**：缺轮/半段/指纹不符都照导（先落权重才是这一半个功能的主价值）。"""
    z = tmp_path / "deliver-broken.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("it-001/weights.json", _weights(1))
        zf.writestr("it-003/weights.json", _weights(3))
        zf.writestr("metrics.jsonl", json.dumps({"it": 1, "weights_fp": "0" * 64}) + "\n")
        zf.writestr("state.json", json.dumps({"run_id": "br-1", "state": "running", "last_it": 9}))
    got = import_deliver_zip(z, tmp_path / "deliver")
    assert got["checks_ok"] is False and got["partial"] is True, "state 自报跑到 it9，包里只到 it3"
    by = {c["id"]: c for c in got["checks"]}
    # 按**数据**判（不按那句人话）：措辞一改就红、而改措辞与行为对错无关（见 text_asserts 口径 A）。
    assert by["continuity"]["missing"] == [2]
    assert by["rows-fp"]["bad_fp"] == [1] and by["rows-fp"]["no_rows"] == [3]
    assert by["state"]["state_last_it"] == 9 and by["state"]["package_last_it"] == 3
    assert by["state-fp"]["ok"] is False
    assert by["anchors"]["ok"] is False, "手工包没有 manifest/plan — 得看得见"
    assert by["weights"]["ok"] is True, "两件权重都在场 ⇒ 这一项不该被别的毛病连坐"
    assert (tmp_path / "deliver" / "br-1" / "it-003" / "weights.json").read_bytes() == _weights(3)


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


def test_cli_transfer_flag_writes_run_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """控制台的第二趟调用形状：`--end-it-reached --run-id … --dest …`（不再传 zip）。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2, 3))
    dest = tmp_path / "demo" / "deliver"
    assert main(["--zip", str(z), "--dest", str(dest), "--course", "demo", "--json-only"]) == 0
    capsys.readouterr()
    rc = main([
        "--end-it-reached",
        "--run-id",
        "x1-demo",
        "--dest",
        str(dest),
        "--course",
        "demo",
        "--json-only",
    ])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out[len(IMPORT_JSON_MARK) :])
    assert payload["written"] is True and payload["it"] == 3 and payload["iters"] == 3


# ────────────────── 落位语义：去 rename + it 级先到者为准（plan §8.2 / §9-12）──────────────────
# 现场（reports/y20-dist2-offline-backfeed-20261016.md §3.8）：Windows 上「解到 <dest>/<run>.tmp-pid
# 再整体改名」100% WinError 5（`tmp/**` 里的目录被监视器按序挂句柄）⇒ 落位改成
# 「逐轮 merge + 整目录零 rename」：不再有任何目录改名/替换，且已有的轮不重写（D5）。


def _no_directory_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    """把**对目录**的 rename/replace 打成 `PermissionError`（模拟 Windows 的 WinError 5）。

    只拦「源或目标是目录」的那一种：文件级的原子写不在本 case 的语义里——现场失败的正是
    `base.rename(final)`（`deliver_zip.py:187`）那种**目录整体改名**。
    """
    real_rename, real_replace = os.rename, os.replace

    def guard(fn):
        def wrapper(src, dst, *a, **kw):
            if Path(src).is_dir() or Path(dst).is_dir():
                raise PermissionError(5, "Access is denied（模拟被占住的目录）", str(src))
            return fn(src, dst, *a, **kw)

        return wrapper

    monkeypatch.setattr(os, "rename", guard(real_rename))
    monkeypatch.setattr(os, "replace", guard(real_replace))


def test_import_never_moves_a_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """导入**不得依赖目录改名**（本机 WinError 5 的根因）——拦掉就应当照常导成功。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2, 3))
    _no_directory_moves(monkeypatch)
    got = import_deliver_zip(z, tmp_path / "deliver")
    assert got["iters"] == [1, 2, 3] and got["last_it"] == 3
    assert (Path(got["dir"]) / "it-003" / "weights.json").read_bytes() == _weights(3)
    assert not list((tmp_path / "deliver").glob("*.tmp-*"))  # 中转目录不残留


def test_second_package_keeps_the_first_bytes_and_adds_only_new_rounds(tmp_path: Path) -> None:
    """DoD-7a：同名轮先到者为准——盘上 it2..3 仍是 A 的字节，it4..5 来自 B。"""
    dest = tmp_path / "deliver"
    a = _make_artifact_zip(tmp_path, iters=(1, 2, 3), tag="a")
    first = import_deliver_zip(a, dest)
    b = _make_artifact_zip(tmp_path, iters=(2, 3, 4, 5), tag="b", salt=b"-B")
    second = import_deliver_zip(b, dest)
    assert second["dir"] == first["dir"]
    assert second["iters"] == [1, 2, 3, 4, 5]
    assert second["kept_rounds"] == 2 and second["skipped_rounds"] == 2
    # 先到的那两份字节一字未改；新的两轮来自后到的包
    assert (Path(second["dir"]) / "it-002" / "weights.json").read_bytes() == _weights(2)
    assert (Path(second["dir"]) / "it-005" / "weights.json").read_bytes() == _weights(5) + b"-B"
    # 有冲突 ⇒ **不**采纳新包的 state.json（否则它会指向被跳过的轮）
    st = json.loads((Path(second["dir"]) / "state.json").read_text(encoding="utf-8"))
    assert st["last_it"] == 3


def test_reimport_is_idempotent_and_writes_no_ledger_row(tmp_path: Path) -> None:
    """DoD-7b：重复导同一个包 ⇒ 幂等（不多写轮次、不多写账本行）。"""
    dest = tmp_path / "demo" / "deliver"
    z = _make_artifact_zip(tmp_path, iters=(1, 2))
    import_deliver_zip(z, dest, course="demo")
    ledger = tmp_path / "demo" / "training_log.jsonl"
    before = ledger.read_bytes()
    again = import_deliver_zip(z, dest, course="demo")
    assert again["kept_rounds"] == 0 and again["skipped_rounds"] == 0
    assert again["healed_files"] == 0
    assert ledger.read_bytes() == before
    assert _ledger_iters(ledger) == [1, 2]


def test_a_round_that_lost_a_file_is_healed_on_reimport(tmp_path: Path) -> None:
    """F6：已存在但**缺件**的轮要补齐（旧回灌腿是「存在即整轮跳过」⇒ 永不愈合）。"""
    z = _make_artifact_zip(tmp_path, iters=(1, 2))
    got = import_deliver_zip(z, tmp_path / "deliver")
    opt = Path(got["dir"]) / "it-002" / "opt.tar"
    opt.unlink()
    again = import_deliver_zip(z, tmp_path / "deliver")
    assert opt.read_bytes() == b"opt-2"
    assert again["kept_rounds"] == 0 and again["healed_files"] >= 1


def test_readings_from_a_later_import_still_reach_the_course_ledger(tmp_path: Path) -> None:
    """读数 additive：`final/metrics.jsonl` 停在上一次的水位时，**新包**的轮次照样进课程账本。

    否则第二次导入的曲线（控制台「各轮指标表」）会静默缺失——而权重都在盘上。
    """
    dest = tmp_path / "demo" / "deliver"
    # 不传 course：文件名里的课程对账是另一条用例的事（这里的标签只用于区分两份包）
    import_deliver_zip(_make_artifact_zip(tmp_path, iters=(1, 2), tag="a"), dest)
    import_deliver_zip(_make_artifact_zip(tmp_path, iters=(3, 4), tag="b"), dest)
    assert _ledger_iters(tmp_path / "demo" / "training_log.jsonl") == [1, 2, 3, 4]


def test_replace_flag_overwrites_with_the_late_package(tmp_path: Path) -> None:
    """DoD-7c：`replace=True` 是人工裁决的逃生门，行为 = 旧覆盖语义（只覆盖**该轮的件**）。"""
    dest = tmp_path / "deliver"
    import_deliver_zip(_make_artifact_zip(tmp_path, iters=(1, 2), tag="a"), dest)
    b = _make_artifact_zip(tmp_path, iters=(1, 2), tag="b", salt=b"-B")
    got = import_deliver_zip(b, dest, replace=True)
    assert got["skipped_rounds"] == 0
    assert (Path(got["dir"]) / "it-001" / "weights.json").read_bytes() == _weights(1) + b"-B"


def test_import_from_run_reports_the_same_landing_counts(tmp_path: Path) -> None:
    """回灌腿与导入腿**同一份判据**（F6）：同样的计数、同样会补缺件。"""
    src = _hub_received_run(tmp_path, iters=(1, 2))
    dest = tmp_path / "deliver"
    got = import_from_run(src, dest, run_id="seg-1")
    assert got["kept_rounds"] == 2 and got["skipped_rounds"] == 0
    (dest / "seg-1" / "it-001" / "opt.tar").unlink()
    again = import_from_run(src, dest, run_id="seg-1")
    assert again["healed_files"] >= 1
    assert (dest / "seg-1" / "it-001" / "opt.tar").read_bytes() == b"opt-1"


def test_cli_replace_flag_is_accepted(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """逃生门要在 CLI 上真的可达（人工裁决时人跑的就是这条命令）。"""
    dest = tmp_path / "deliver"
    import_deliver_zip(_make_artifact_zip(tmp_path, iters=(1, 2), tag="a"), dest)
    b = _make_artifact_zip(tmp_path, iters=(1, 2), tag="b", salt=b"-B")
    rc = main(["--zip", str(b), "--dest", str(dest), "--replace", "--json-only"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out[len(IMPORT_JSON_MARK) :])
    assert payload["skipped_rounds"] == 0
    assert (dest / "x1-demo" / "it-001" / "weights.json").read_bytes() == _weights(1) + b"-B"


def test_cli_transfer_without_run_id_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = main(["--end-it-reached", "--dest", str(tmp_path / "deliver")])
    assert rc == 2 and "--run-id" in capsys.readouterr().err

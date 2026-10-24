"""tests/remote/test_deliver_outbox.py —— push 腿的**本地 outbox**（2026-10-23，W3）。

钉住 `plan/push-mode-notebooks.plan.md` §6.4a 的四条（评审 P0-1/P0-3/P1-1/P1-3/P1-10）：

  1. **与 HTTP 腿同字节**：同一份产物目录 + 同一个时钟 ⇒ `rounds/it0001.json` 与
     `POST /offline/artifact` 的体、`final.json` 与 `POST /offline/result` 的体**逐字节相同**
     （装配只有 `deliver_parts` 一份，这里量的是它真的被两边用上）；
  2. **rev 与 ack**：`rev` 只在重投时前进；ack **带 rev** 只删「看到的就是当前版本」的那几轮——
     迟到的评估行让 rev+1 之后，旧 rev 的 ack **不得**把新体删掉（那是 hub 要重拉的那一格）；
  3. **统计面**：每轮都有 `metrics/itNNNN.json`（`oversize` 的轮也有 ⇒ 控制台仍看得见那一轮）；
  4. **进程内降级是常态**（P1-10）：`background=True`（线程模式）与子进程模式写的是**同一个**
     outbox、**同一份**实现 —— 降级后 outbox 不能是空的。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import OFFLINE_ARTIFACT_PATH, OFFLINE_RESULT_PATH
from remote import deliver_parts
from remote.deliver_outbox import (
    FINAL_NAME,
    STATE_NAME,
    LocalOutboxDeliverer,
    ack_outbox,
    metrics_path,
    read_state,
    round_path,
)
from remote.offline_deliver import OfflineDeliverer
from tests.remote.test_offline_deliver import RUN, _make_artifacts  # type: ignore

#: 两边共用的时钟（`ts` 是体里唯一随时间变的字段；不给同一个值就没法比字节）。
NOW = 1_234_567.5


def _quiet(_msg: str) -> None:
    """日志静音（断言失败时直接改成 print 看细节）。"""


class _Recorder:
    """`OfflineDeliverer` 的 opener 替身：记账 URL → 体，并假装 hub 一切正常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    def __call__(self, url: str, data: bytes, _headers: dict, _timeout: float):
        self.calls.append((url, bytes(data or b"")))
        return 200, b"{}"

    def body_for(self, path: str) -> bytes:
        """某个端点收到的**最后一个**请求体（没有 ⇒ 空，让断言用一句人话报错）。"""
        hits = [body for url, body in self.calls if url.endswith(path)]
        assert hits, f"{path} 一次都没被调用（实际：{[u for u, _ in self.calls]}）"
        return hits[-1]


def _outbox(tmp_path: Path, root: Path, **kw: object) -> LocalOutboxDeliverer:
    """构造被测对象（缺省：同步模式、固定时钟、日志静音）。"""
    return LocalOutboxDeliverer(
        outbox_dir=tmp_path / "outbox",
        artifacts_dir=root,
        run_id=RUN,
        parts=deliver_parts,
        now=lambda: NOW,
        log=_quiet,
        **kw,  # type: ignore[arg-type]
    )


def _http_leg(root: Path, rec: _Recorder, **kw: object) -> OfflineDeliverer:
    """同一份产物目录的 HTTP 腿（同一个时钟；`opener` 打桩 ⇒ 不碰网络）。"""
    return OfflineDeliverer(
        base_url="http://hub.local",
        token="t",
        run_id=RUN,
        artifacts_dir=root,
        opener=rec,
        now_fn=lambda: NOW,
        log=_quiet,
        **kw,  # type: ignore[arg-type]
    )


def _add_matching_row(root: Path, it: int) -> None:
    """追加一条**指纹对得上本轮权重**的账本行（`row_for` 的判据；`_make_artifacts` 的行没有 fp）。

    为什么要它：统计体的内容就是这个 row，而 `row_for` 宁可返回 None 也不发一条指错轮次的行
    ——不补这一行就测不到「outbox 带统计」这件事本身。
    """
    fp = hashlib.sha256((root / f"it-{int(it):03d}" / "weights.json").read_bytes()).hexdigest()
    with (root / "metrics.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"it": int(it), "weights_fp": fp, "wall_sec": 2.0}) + "\n")


def _add_eval(root: Path, it: int) -> None:
    """给某轮追加一条 A 层评估读数（迟到的那一条；`eval_rows_for` 的判据是 `iter` + 无 `source`）。"""
    with (root / "eval_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "eval", "iter": int(it), "stage": 1, "win": True}) + "\n")


# ───────────────────────────── ① 与 HTTP 腿同字节 ─────────────────────────────


def test_round_body_is_byte_identical_with_the_http_leg(tmp_path: Path) -> None:
    """`rounds/it0001.json` == `POST /offline/artifact` 的体（**逐字节**，不是逐字段）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1

    rec = _Recorder()
    http = _http_leg(root, rec)
    assert http.sync() == 1

    on_disk = round_path(tmp_path / "outbox", 1).read_bytes()
    assert on_disk == rec.body_for(OFFLINE_ARTIFACT_PATH), (
        "两条腿的体不再同字节 ⇒ 装配器没有共用（见 deliver_parts.build_round_body）"
    )
    body = json.loads(on_disk.decode("utf-8"))
    assert int(body["it"]) == 1 and body["weights_json"]


def test_result_body_is_byte_identical_with_the_http_leg(tmp_path: Path) -> None:
    """`final.json` == `POST /offline/result` 的体（含 `delivered` 这个会变的计数）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    ob.submit_final(
        it_end=1, state="complete", summary={"why": "ok"}, end_it_reached=True, seg_end_kind="course"
    )

    rec = _Recorder()
    http = _http_leg(root, rec)
    assert http.sync() == 1
    assert http.deliver_result(
        it_end=1, state="complete", summary={"why": "ok"}, end_it_reached=True, seg_end_kind="course"
    )

    assert (tmp_path / "outbox" / FINAL_NAME).read_bytes() == rec.body_for(OFFLINE_RESULT_PATH)
    assert json.loads((tmp_path / "outbox" / FINAL_NAME).read_text(encoding="utf-8"))["delivered"] == 1


# ───────────────────────────── ② 状态 / rev / ack ─────────────────────────────


def test_state_records_rev_sha_and_the_metrics_file(tmp_path: Path) -> None:
    """落盘后 `state.json` 能自证（rev/sha/bytes），且统计体已经可以单独取走。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _add_matching_row(root, 1)
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1

    st = read_state(tmp_path / "outbox")
    ent = st["rounds"]["1"]
    raw = round_path(tmp_path / "outbox", 1).read_bytes()
    assert ent["rev"] == 1
    assert ent["bytes"] == len(raw)
    assert ent["sha256"] == hashlib.sha256(raw).hexdigest()
    assert ent["acked_rev"] == 0 and ent["oversize"] is False
    assert st["updated_at"] == NOW  # 时间来自注入的时钟（不然这条断言一直在比墙钟）

    metrics = json.loads(metrics_path(tmp_path / "outbox", 1).read_text(encoding="utf-8"))
    assert metrics["run_id"] == RUN
    assert [int(r["it"]) for r in metrics["rows"]] == [1]
    assert metrics["rows"][0]["row"]["it"] == 1


def test_ack_with_the_seen_rev_removes_body_and_metrics(tmp_path: Path) -> None:
    """hub 看到的就是当前版本 ⇒ 删体 + 删统计，条目留 `acked_rev`（判据在 hub 侧）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _add_matching_row(root, 1)
    _add_matching_row(root, 2)
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 2

    got = ob.ack(1, revs={1: 1})
    assert got["removed"] == [1] and got["kept"] == [] and got["metrics_removed"] == 1
    assert not round_path(out, 1).exists() and not metrics_path(out, 1).exists()
    assert round_path(out, 2).exists(), "it2 在 upto_it 之外，不许被删"
    ent = read_state(out)["rounds"]["1"]
    assert ent["rev"] == 1 and ent["acked_rev"] == 1 and ent["file"] == ""
    assert ob.status()["delivered"] == 1


def test_a_late_eval_row_bumps_rev_and_a_stale_ack_must_not_delete_it(tmp_path: Path) -> None:
    """P0-3 的正身：真实窗口是「权重体已 ack → 评估落账 → rev+1 → hub 必须重拉」。

    旧实现（无条件删 `it <= upto_it`）会把刚建好的那一格吃掉 —— 读数永久丢。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    ob.ack(1, revs={1: 1})
    assert not round_path(out, 1).exists()

    _add_eval(root, 1)
    ob.mark_repost(1)  # 子进程循环里 = 收到 `{"repost": 1}` 那一行
    assert ob.sync() == 1
    ent = read_state(out)["rounds"]["1"]
    assert ent["rev"] == 2, "重投必须让 rev 前进（否则 hub 分不出新旧）"
    assert round_path(out, 1).exists()
    body = json.loads(round_path(out, 1).read_text(encoding="utf-8"))
    assert body["eval_rows"], "重投的体必须带上刚到的评估行"

    # hub 只看过 rev1：它手里的 rev 落后 ⇒ **不删**，等它按 rev2 重拉。
    got = ob.ack(1, revs={1: 1})
    assert got["kept"] == [1] and got["removed"] == []
    assert round_path(out, 1).exists()
    # 它按 rev2 再来一次 ⇒ 收下并清空。
    assert ob.ack(1, revs={1: 2})["removed"] == [1]
    assert not round_path(out, 1).exists()


def test_ack_of_an_unseen_round_keeps_the_body(tmp_path: Path) -> None:
    """`revs` 里根本没有这一轮（hub 拉漏了）⇒ 也不许删（删了就等于替 hub 假装收到）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    got = ob.ack(1, revs={})
    assert got["kept"] == [1] and got["removed"] == []
    assert round_path(out, 1).exists()


def test_final_is_deleted_only_when_result_is_acknowledged(tmp_path: Path) -> None:
    """`result=true` 才删 `final.json`（段末摘要是「这条腿到哪了」的唯一答案）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    ob.submit_final(it_end=1, state="complete")
    assert (out / FINAL_NAME).exists()

    assert ob.ack(1, result=False)["final_removed"] is False
    assert (out / FINAL_NAME).exists() and ob.result_done() is False

    assert ob.ack(1, result=True)["final_removed"] is True
    assert not (out / FINAL_NAME).exists() and ob.result_done() is True
    assert read_state(out)["final_acked"] is True


def test_result_ack_without_a_final_is_a_terminal_marker_that_survives_a_restart(
    tmp_path: Path,
) -> None:
    """W6：`result=true` 是**整单**的终态 —— failed/cancelled 没有 final 也走这条。

    face 重启后按盘上的 `result_acked` 跳过已收摊的目录（否则废弃单被复活成 busy，worker
    永远不接下一单）。所以记号必须落在**盘上**，且经得起另一个写者的整份覆盖：
    `_save_state` 的合并（`_merge_disk_acks`）要保住它，新实例也要从盘上读出来。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    # 没写 final 的收摊（failed / cancelled）：`upto_it=0` 不删任何轮
    got = ack_outbox(out, upto_it=0, result=True, revs={})
    assert got["final_removed"] is False
    st = read_state(out)
    assert st["result_acked"] is True and st["final_ready"] is False
    assert st["final_acked"] is False, "没有 final 时不许把两件事混成同一个记号"

    # 「重启」= 新实例读同一份 outbox；它自己的任何一次整份写都不许把终态冲回去
    again = _outbox(tmp_path, root)
    again._save_state()
    assert read_state(out)["result_acked"] is True


def test_a_stale_save_cannot_resurrect_what_the_face_process_acked(tmp_path: Path) -> None:
    """F8：训练子进程拿着 ack **之前**的快照再整份写 ⇒ 必须并回服务进程写的 ack。

    真实窗口：`/offline/push/ack`（服务进程）删了 body/统计/final 并写账本；训练子进程的
    `_save_state()`（任何一个 submit 都会走到）随后整份覆盖 —— 不合并就会把 `file`/`final_ready`
    写回去，hub 再拉永远 404，pump 永不收尾。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _add_matching_row(root, 1)
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    ob.submit_final(it_end=1, state="complete")
    assert round_path(out, 1).exists() and (out / FINAL_NAME).exists()

    # 服务进程（另一个写者）收下 rev1：删 body + 统计 + final
    got = ack_outbox(out, upto_it=1, result=True, revs={1: 1})
    assert got["removed"] == [1] and got["final_removed"] is True
    assert not round_path(out, 1).exists() and not (out / FINAL_NAME).exists()

    # 训练子进程的旧快照（file / final_ready 都还在）整份写回
    ob._save_state()

    st = read_state(out)
    ent = st["rounds"]["1"]
    assert ent["file"] == "" and ent["acked_rev"] == 1
    assert st["final_ready"] is False and st["final_acked"] is True
    assert not round_path(out, 1).exists() and not (out / FINAL_NAME).exists()

    # 但「rev 前进」的那一格不能被误抹：迟到评估行重投的新 body 必须留在盘上等 hub 重拉
    _add_eval(root, 1)
    ob.mark_repost(1)
    assert ob.sync() == 1
    ent = read_state(out)["rounds"]["1"]
    assert ent["rev"] == 2 and ent["file"], "rev2 的新体被旧 ack 的合并误删了"
    assert round_path(out, 1).exists()


# ───────────────────────────── ③ 统计 / 超限 / 判据 ─────────────────────────────


def test_oversize_round_is_recorded_without_a_body_but_keeps_metrics(tmp_path: Path) -> None:
    """超 `OFFLINE_ARTIFACT_BODY_MAX` 的轮：不落 body、**照样记统计**（y31-null 的教训）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _add_matching_row(root, 1)
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root, cap_bytes=64)
    assert ob.sync() == 1

    assert not round_path(out, 1).exists()
    ent = read_state(out)["rounds"]["1"]
    assert ent["oversize"] is True and ent["file"] == ""
    assert metrics_path(out, 1).exists(), "超限的轮若连统计一起丢，控制台就看不到这一轮"
    assert ob.pending() == [], "已记账的轮不要再试（否则每拍白读一遍权重）"


def test_start_checkpoint_without_a_ledger_row_is_never_materialized(tmp_path: Path) -> None:
    """起点（有权重、无账本行）不是训练轮 ⇒ 不进 outbox（2026-10-11 事故的形状）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    start = root / "it-002"
    start.mkdir()
    (start / "weights.json").write_bytes(b'{"anchor":true}')
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root)
    assert ob.sync() == 1
    assert not round_path(out, 2).exists()
    assert ob.pending() == []


def test_floor_it_skips_rounds_the_hub_already_has_and_it_sticks(tmp_path: Path) -> None:
    """`floor_it` = hub 的 cursor（派发时给）：≤ 它的轮不再重发，且**重启后仍然是它**。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root, floor_it=1)
    assert ob.pending() == [2]
    assert ob.sync() == 1
    assert not round_path(out, 1).exists() and round_path(out, 2).exists()
    assert read_state(out)["floor_it"] == 1

    again = _outbox(tmp_path, root)  # 重启不带参：地板从 state 里读回来
    assert again.pending() == []
    assert read_state(out)["floor_it"] == 1, "地板一旦落盘就不许被一次「没传参」冲回 0"


def test_reload_picks_up_the_child_writes(tmp_path: Path) -> None:
    """父侧镜像靠 `reload()` 追上子进程（否则 `status()` 永远读**构造那一刻**的快照）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    reader = _outbox(tmp_path, root)  # 先建镜像（此时 state.json 还不存在）
    assert reader.pending() == [1]

    writer = _outbox(tmp_path, root)
    assert writer.sync() == 1
    assert reader.pending() == [1], "镜像不 reload 就看不见子进程写的 state.json"

    reader.reload()
    assert reader.pending() == []
    assert reader.status()["rounds"]["1"]["rev"] == 1


# ───────────────────────────── ④ 进程内降级（P1-10） ─────────────────────────────


def test_background_mode_writes_the_same_files_without_blocking(tmp_path: Path) -> None:
    """线程模式（进程内降级的那个形态）必须**真的产出** body —— 否则就是「降级成功、outbox 空」。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    ob = _outbox(tmp_path, root, background=True)
    ob.start()
    try:
        ob.submit_round(1)  # 只置位 + 唤醒（训练线程不等文件 IO）
        end = time.time() + 10.0
        while time.time() < end and not round_path(out, 1).exists():
            # sleep-ok: 轮询步长（等的是线程真的写了文件；超时只当挂起兜底）
            time.sleep(0.02)
        assert round_path(out, 1).exists(), "后台模式没写出 body"
        ob.submit_final(it_end=1, state="complete")
        end = time.time() + 10.0
        while time.time() < end and not (out / FINAL_NAME).exists():
            # sleep-ok: 轮询步长（等的是线程把 final.json 写出来；超时只当挂起兜底）
            time.sleep(0.02)
        assert (out / FINAL_NAME).exists()
    finally:
        ob.close(5.0)
    assert ob._thread is None, "收线必须把线程停掉（否则进程退不干净）"
    assert read_state(out)["rounds"]["1"]["rev"] == 1


def test_close_reports_unsent_work_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收线时「还有轮没物化」必须说出来（静默收线 = 看得见的数据损失）。

    正常收线会先 flush 一拍（那时就没有欠账了）——这里把物化打桩成失败，模拟「盘/IO 不听话」。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    logs: list[str] = []
    ob = LocalOutboxDeliverer(
        outbox_dir=tmp_path / "outbox",
        artifacts_dir=root,
        run_id=RUN,
        parts=deliver_parts,
        now=lambda: NOW,
        log=logs.append,
    )
    monkeypatch.setattr(ob, "_write_round", lambda _it: False)
    ob.close(0.0)
    assert any("还有 1 轮没物化" in m for m in logs), logs
    assert not round_path(tmp_path / "outbox", 1).exists(), "打桩失败就不该有 body"

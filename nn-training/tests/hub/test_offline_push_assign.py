"""tests/hub/test_offline_push_assign.py —— 离线 push 的在途 assignment 账本（W5）。

账本的唯一职责是「hub 重启后还知道自己在拉哪一单、拉到哪」。所以这里钉四件事：

1. **落盘可读回**（换一个实例 = 换一个进程）；
2. `cursor_it` / `revs` 只经 `set_fields` 推进（没有条目 ⇒ None，不凭空造）；
3. 终局仍留在表里（`mark(..., "conflict")` 之后人还能看到），只有 `drop` 才消失；
4. **坏文件按空表起**（一个半截 JSON 不该让 hub 起不来）。
"""

from __future__ import annotations

import json
from pathlib import Path

from hub.offline_push_assign import OfflinePushAssignments, assign_path

COURSE = "c5-gae"


def _rec(**kw) -> dict:
    base = {
        "course": COURSE,
        "worker_id": "push:offline-kaggle",
        "task_id": "0123456789abcdef",
        "pack_sha256": "abc",
        "run_id": "run-1",
        "lease_token": "t1",
        "cursor_it": 0,
        "revs": {},
        "phase": "running",
    }
    base.update(kw)
    return base


def test_assignment_survives_a_restart(tmp_path: Path) -> None:
    path = assign_path(tmp_path, 8765)
    assert path.name == "offline-push.8765.json"
    a = OfflinePushAssignments(path)
    a.put(_rec())
    a.set_fields(COURSE, cursor_it=3, revs={"1": 0, "2": 1, "3": 0})

    b = OfflinePushAssignments(path)  # 另一个实例 = 另一个进程
    rec = b.get(COURSE)
    assert rec is not None
    assert rec["cursor_it"] == 3
    assert rec["revs"] == {"1": 0, "2": 1, "3": 0}
    assert rec["worker_id"] == "push:offline-kaggle"


def test_set_fields_on_a_missing_assignment_does_not_create_one(tmp_path: Path) -> None:
    a = OfflinePushAssignments(assign_path(tmp_path, 1))
    assert a.set_fields("nope", phase="done") is None
    assert a.snapshot() == {}


def test_mark_keeps_the_record_and_drop_removes_it(tmp_path: Path) -> None:
    a = OfflinePushAssignments(assign_path(tmp_path, 2))
    a.put(_rec())
    marked = a.mark(COURSE, "conflict", conflict="it3 内容不同")
    assert marked is not None and marked["phase"] == "conflict"
    assert a.get(COURSE) is not None  # 终局仍在表里（人要在读面看到它）
    assert a.drop(COURSE) is True
    assert a.drop(COURSE) is False
    assert a.snapshot() == {}


def test_of_worker_filters_by_holder(tmp_path: Path) -> None:
    a = OfflinePushAssignments(assign_path(tmp_path, 3))
    a.put(_rec())
    a.put(_rec(course="c6-gae", worker_id="push:offline-colab"))
    assert set(a.of_worker("push:offline-kaggle")) == {COURSE}
    assert set(a.of_worker("push:offline-colab")) == {"c6-gae"}
    assert a.of_worker("push:nobody") == {}


def test_corrupt_file_starts_from_an_empty_table(tmp_path: Path) -> None:
    path = assign_path(tmp_path, 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"c5-gae": {"phase": "running"', encoding="utf-8")  # 半截写入
    a = OfflinePushAssignments(path)
    assert a.snapshot() == {}
    # 坏文件不影响后续写入（下一次原子写会把整份换新）
    a.put(_rec())
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc[COURSE]["run_id"] == "run-1"

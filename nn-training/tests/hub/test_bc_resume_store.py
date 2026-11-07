"""BC 接续训练的 hub 存储语义（**免 torch**）—— 2026-09-26 自 `test_bc_epoch_resume.py` 分家。

分家理由：这几条只碰 `_JobStore`（磁盘 resume 单文件 + 指标 jsonl + 租约门，时间用假
时钟注入、不碰 HTTP 层），却在原文件里与 `from train.bc import train`（真 torch 训练）
同住 ⇒ 无 torch 机上**整个文件收集失败**，白丢 6 条纯存储/课程用例。

不变式：
  * `store_bc_epoch` 覆盖语义 —— 磁盘恒 1 份 resume（最新 epoch 胜），指标行按 epoch **追加**
    （同一 epoch 的迟到重发幂等：隧道 530 的两次投递不得在控制台显示成两行）；
  * 迟到的旧 epoch 回传**不得倒回** resume（补发链路：ep6 之后补发 ep5）——指标行照补；
  * 体校验（epoch<1 / 缺 weights / bool 冒充 int）→ 拒收且不留痕；
  * 租约门 —— 活租约期非持有人不得写 resume；无租约（过期/重启后）照收（幂等覆盖）；
  * 无 resume ⇒ worker GET 404 语义（全新训练）。

真训练那一半（`bc_train` 的 on_epoch 接续编号）留在 `test_bc_epoch_resume.py`。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from hub.server import _JobStore


def _clock(t: float = 1000.0):
    class _C:
        def __init__(self) -> None:
            self.t = t

        def __call__(self) -> float:
            self.t += 0.001
            return self.t

    return _C()

def _mini_store(tmp_path: Path) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=_clock())

def _epoch_body(epoch: int) -> dict:
    return {
        "epoch": epoch,
        "weights": "H4sIAAAAAAAA//KtKs7PLUlN0clMLQZxQUJOBgDlGt0eCwAAAA==",  # gzip(b'{}')
        "metrics": {
            "train_loss": 1.5,
            "val_loss": 1.4,
            "move_acc": 0.5,
            "fire_acc": 0.7,
            "lr": 3e-3,
        },
    }

def test_store_bc_epoch_roundtrip_and_overwrite(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(1)) is True
    first = store.get_bc_resume(jid)
    assert first is not None and first["epoch"] == 1
    # 覆盖语义：最新 epoch 胜（磁盘有界：恒 1 份 resume）
    assert store.store_bc_epoch(jid, _epoch_body(7)) is True
    assert store.get_bc_resume(jid)["epoch"] == 7  # type: ignore[index]
    # 指标行追加（不覆盖）
    rows = store.get_bc_metrics(jid)
    assert [r["epoch"] for r in rows] == [1, 7]
    assert rows[0]["move_acc"] == 0.5

def test_store_bc_epoch_invalid_body_rejected(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, {"epoch": 0}) is False  # epoch < 1
    assert store.store_bc_epoch(jid, {"epoch": 3}) is False  # 缺 weights
    assert store.store_bc_epoch(jid, {"epoch": True, "weights": "x"}) is False  # bool 冒充 int
    assert store.get_bc_resume(jid) is None and store.get_bc_metrics(jid) == []
    assert not (tmp_path / "jobs" / jid / "epoch-weights").exists()


def test_every_epoch_gets_an_immutable_snapshot(tmp_path: Path) -> None:
    """#12：resume 只留最新，但每个 epoch（含迟到补发）都留一份不可变快照。"""
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(1)) is True
    assert store.store_bc_epoch(jid, _epoch_body(7)) is True
    assert store.store_bc_epoch(jid, _epoch_body(5)) is True  # 迟到的补发

    snaps = sorted(p.name for p in (tmp_path / "jobs" / jid / "epoch-weights").glob("*.json"))
    assert snaps == ["ep1.json", "ep5.json", "ep7.json"]
    assert store.get_bc_epoch_weights(jid, 5)["epoch"] == 5  # type: ignore[index]
    assert store.get_bc_resume(jid)["epoch"] == 7  # type: ignore[index]


def test_epoch_snapshot_same_epoch_replay_is_one_file(tmp_path: Path) -> None:
    """同一 epoch 的重发（隧道 530）原子覆盖同名快照，不产生第二份。"""
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(3)) is True
    body = _epoch_body(3)
    body["metrics"]["val_loss"] = 1.2
    assert store.store_bc_epoch(jid, body) is True
    files = list((tmp_path / "jobs" / jid / "epoch-weights").glob("ep3*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["metrics"]["val_loss"] == 1.2
    assert store.get_bc_epoch_weights(jid, 0) is None
    assert store.get_bc_epoch_weights(jid, 99) is None

def test_store_bc_epoch_same_epoch_replay_appends_once(tmp_path: Path) -> None:
    """隧道 530 的「已落盘但响应丢了」重发：resume 照写，指标行不得重复。

    现场：bc-v6-train 的 ep2/ep4 在账本里各出现两行（worker 收到 530 后重发同一 body，
    hub 每次 append 一行）。重复行会吃掉控制台「最新 6 个 epoch」的名额。
    """
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(2)) is True
    assert store.store_bc_epoch(jid, _epoch_body(2)) is True  # 重发是合法请求
    rows = store.get_bc_metrics(jid)
    assert [r["epoch"] for r in rows] == [2]
    assert store.get_bc_resume(jid)["epoch"] == 2  # type: ignore[index]


def test_store_bc_epoch_late_replay_does_not_rewind_resume(tmp_path: Path) -> None:
    """补发漏传的旧 epoch：指标行补上，但 resume 必须停在最新 epoch。

    否则 worker 重启/换机重领时会从 ep5 接着训已训完的 ep6——白烧一整轮算力，
    而 resume 的唯一职责就是「最新权重」。
    """
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.store_bc_epoch(jid, _epoch_body(4)) is True
    assert store.store_bc_epoch(jid, _epoch_body(6)) is True
    assert store.store_bc_epoch(jid, _epoch_body(5)) is True  # 隧道恢复后补发的 ep5
    assert store.get_bc_resume(jid)["epoch"] == 6  # type: ignore[index]
    assert [r["epoch"] for r in store.get_bc_metrics(jid)] == [4, 6, 5]


def test_bc_epoch_lease_gate(tmp_path: Path) -> None:
    """活租约期非持有人不得写 resume（防被顶掉的旧 worker 用旧 epoch 覆盖新 resume）；
    无租约（过期/重启后）照收——resume 是幂等覆盖存最新。"""
    store = _mini_store(tmp_path)
    jid = "j" * 16
    jd = tmp_path / "jobs" / jid
    jd.mkdir(parents=True)
    (jd / "manifest.json").write_text("{}", encoding="utf-8")
    tok = store.claim(jid, ttl=300)
    assert tok is not None
    assert store.result_token_ok(jid, "wrong-token") is False
    assert store.result_token_ok(jid, tok) is True
    store.release(jid, tok)
    assert store.result_token_ok(jid, "wrong-token") is True  # 无租约照收

def test_bc_resume_missing_is_404_semantic(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    (tmp_path / "jobs" / jid).mkdir(parents=True)
    assert store.get_bc_resume(jid) is None  # worker GET 404 → 全新训练


def _job_with_manifest(
    tmp_path: Path,
    jid: str,
    *,
    key: str = "bc-x",
    it: int = 1,
    epochs: int = 80,
) -> Path:
    jd = tmp_path / "jobs" / jid
    jd.mkdir(parents=True)
    (jd / "manifest.json").write_text(
        json.dumps(
            {
                "job_id": jid,
                "kind": "bc",
                "course_key": key,
                "it": it,
                "epochs": epochs,
            }
        ),
        encoding="utf-8",
    )
    return jd


def test_store_bc_epoch_writes_course_index_and_late_replay_does_not_rewind(
    tmp_path: Path,
) -> None:
    """课程级索引与 per-jid 同写；迟到的 ep5 不得把索引从 ep6 拉回去。"""
    store = _mini_store(tmp_path)
    jid = "j" * 16
    _job_with_manifest(tmp_path, jid)
    assert store.store_bc_epoch(jid, _epoch_body(4)) is True
    index = tmp_path / "jobs" / "bc-resume" / "by-course" / "bc-x" / "1.json"
    assert json.loads(index.read_text(encoding="utf-8"))["epoch"] == 4
    assert store.store_bc_epoch(jid, _epoch_body(6)) is True
    assert json.loads(index.read_text(encoding="utf-8"))["epoch"] == 6
    assert store.store_bc_epoch(jid, _epoch_body(5)) is True  # 迟到补发
    assert json.loads(index.read_text(encoding="utf-8"))["epoch"] == 6
    assert store.get_bc_resume(jid)["epoch"] == 6  # type: ignore[index]


def test_new_jid_reads_course_index_when_per_job_resume_missing(tmp_path: Path) -> None:
    """重开的新 jid 没有自己的 resume ⇒ 回退到同一 `(course,it)` 的索引。

    G4：旧 job 在 hub 侧 unknown/closed 后重发是常态，**旧 job 目录可能已被清掉**
    （`prune_remote_jobs` / 人工清理）。课程索引住 `jobs/bc-resume/by-course`，
    不在单个 job 目录里 ⇒ 源目录消失也不影响接续。
    """
    store = _mini_store(tmp_path)
    old = "o" * 16
    new = "n" * 16
    _job_with_manifest(tmp_path, old)
    assert store.store_bc_epoch(old, _epoch_body(40)) is True
    shutil.rmtree(tmp_path / "jobs" / old)  # 源 job 已不可认领（G4）
    _job_with_manifest(tmp_path, new)
    got = store.get_bc_resume(new)
    assert got is not None and got["epoch"] == 40
    assert not (tmp_path / "jobs" / new / store.BC_RESUME_NAME).exists()


def test_clear_course_index_removes_only_that_course_alias(tmp_path: Path) -> None:
    """#8：清空只删课程级别名；per-jid resume / 别的课 / 幂等语义都不受影响。"""
    store = _mini_store(tmp_path)
    jid = "c" * 16
    _job_with_manifest(tmp_path, jid)
    assert store.store_bc_epoch(jid, _epoch_body(3)) is True
    idx = tmp_path / "jobs" / "bc-resume" / "by-course" / "bc-x" / "1.json"
    other = tmp_path / "jobs" / "bc-resume" / "by-course" / "bc-y" / "2.json"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("{}", encoding="utf-8")
    per_job = tmp_path / "jobs" / jid / store.BC_RESUME_NAME
    assert idx.exists() and per_job.exists()
    assert store.clear_bc_course_index("bc-x") == 1
    assert not idx.exists()
    assert other.exists() and per_job.exists()
    assert store.get_bc_resume(jid)["epoch"] == 3  # type: ignore[index]
    assert store.clear_bc_course_index("bc-x") == 0  # 幂等


def test_course_index_rejects_unsafe_course_key(tmp_path: Path) -> None:
    store = _mini_store(tmp_path)
    jid = "j" * 16
    _job_with_manifest(tmp_path, jid, key="../escape")
    assert store.store_bc_epoch(jid, _epoch_body(1)) is True
    assert not (tmp_path / "jobs" / "bc-resume").exists()

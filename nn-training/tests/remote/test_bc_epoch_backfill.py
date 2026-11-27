"""BC 每-epoch 回传的丢包补发（2026-11-04，**免 torch**）。

现场（bc-v6-train 2026-11-04）：cloudflared 抖动让 `POST /jobs/{id}/epoch` 收到 HTTP 530。
530 有两种形态，都要活下来：

* **响应丢了**（请求已落盘）⇒ 重试会写第二行（控制台重复 ep2/ep4）——hub 侧按 epoch 幂等；
* **请求丢了**（hub 没有这一行）⇒ 这一 epoch 从此永久缺失（ep5）——本文件的 backlog：
  把失败的那一份 body 留到下一次回传成功后重放，训练照跑、不阻塞在隧道上。

钉四件事：失败可判别（bool）· 重放逐条发同一条路径 · 隧道仍断则整批保留 · backlog 有界。
"""

from __future__ import annotations

import json

import remote.bc_job as bc_job
from remote.seams import PortPair


def _body(epoch: int, loss: float = 1.0) -> dict:
    return {
        "epoch": epoch,
        "weights": "H4sIAAAAAAAA//KtKs7PLUlN0clMLQZxQUJOBgDlGt0eCwAAAA==",  # gzip(b'{}')
        "metrics": {
            "train_loss": loss,
            "val_loss": loss,
            "move_acc": 0.5,
            "fire_acc": 0.6,
            "lr": 3e-4,
        },
    }


def _never_sleep(monkeypatch) -> None:
    """`_bc_post_epoch` 的重试间隔（2s）在测试里不该真睡。"""
    monkeypatch.setattr(bc_job.time, "sleep", lambda _s: None)


def test_post_epoch_reports_failure_on_tunnel_530(monkeypatch) -> None:
    """回传失败必须**可判别**（返回 False）——否则调用方无从决定要不要进 backlog。"""
    _never_sleep(monkeypatch)
    paths: list[str] = []

    def fake_request(spec, **_kw):
        paths.append(spec.path)
        return 530, b"<html>cloudflare: origin unreachable</html>"

    monkeypatch.setattr(bc_job, "WIRE", PortPair(fake_request))
    jid = "j" * 16
    ok = bc_job._bc_post_epoch("http://hub", "t", jid, _body(5), "tok", log=lambda _m: None)
    assert ok is False
    assert paths == [f"/jobs/{jid}/epoch"] * 2  # 原有语义：2 次尝试后放弃（不无限重试）


def test_backlog_replay_posts_every_missing_epoch_then_clears(monkeypatch) -> None:
    """隧道恢复后逐个重放同一端点、同一 body；全部送达即清空（下一拍不再重复发）。"""
    _never_sleep(monkeypatch)
    posted: list[dict] = []

    def fake_request(spec, **_kw):
        posted.append(json.loads(spec.data))
        return 200, b"{}"

    monkeypatch.setattr(bc_job, "WIRE", PortPair(fake_request))
    pending = [_body(5), _body(6)]
    left = bc_job._bc_backlog_replay(
        "http://hub", "t", "j" * 16, pending, "tok", log=lambda _m: None
    )
    assert left == 0
    assert pending == []
    assert [b["epoch"] for b in posted] == [5, 6]


def test_backlog_replay_keeps_rows_when_tunnel_still_down(monkeypatch) -> None:
    """隧道还断着（再 530）⇒ **一行都不丢**：整批留在 backlog，返回剩余行数。"""
    _never_sleep(monkeypatch)
    calls: list[str] = []

    def fake_request(spec, **_kw):
        calls.append(spec.path)
        return 530, b""

    monkeypatch.setattr(bc_job, "WIRE", PortPair(fake_request))
    pending = [_body(5), _body(6)]
    left = bc_job._bc_backlog_replay(
        "http://hub", "t", "j" * 16, pending, "tok", log=lambda _m: None
    )
    assert left == 2
    assert [b["epoch"] for b in pending] == [5, 6]
    # 第一条就失败 ⇒ 不接着捶剩下的（隧道断着时多打两次只是拖慢训练）
    assert calls == [f"/jobs/{'j' * 16}/epoch"] * 2


def test_backlog_is_bounded_and_drops_the_oldest() -> None:
    """长断网时 backlog 有界（每份 body 含 ~0.7MB b64 权重）——丢最老的并告警，不无限涨。"""
    pending: list[dict] = []
    for ep in range(1, bc_job.BC_EPOCH_BACKLOG_MAX + 3):
        bc_job._bc_backlog_add(pending, _body(ep))
    assert len(pending) == bc_job.BC_EPOCH_BACKLOG_MAX
    assert pending[0]["epoch"] == 3
    assert pending[-1]["epoch"] == bc_job.BC_EPOCH_BACKLOG_MAX + 2

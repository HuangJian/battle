"""tests/test_offline_deliver_async.py —— 补传**与 PPO 并行**（2026-09-22 用户指令）。

缺口：`OfflineDeliverer` 原先只有同步 `sync()`，而 `run_loop._checkpoint()` 是**内联**调的
——每轮落盘后最多 4 趟 POST（各 ~1.9MB、60s 超时）跑在下一轮 PPO 之前。回传与训练没有
任何数据依赖，那份等待纯属白花。

现在：训练线程只 `submit_round()` 入队（非阻塞），一个 `offline-deliver` daemon 线程独自
拥有补传状态（单写者），段末 `close(timeout)` 做**有界** flush。本文件钉四件事：

  1. `submit_round()` **不等网络**（慢 hub 下入队耗时与 POST 时长无关）；
  2. 后台线程里的任何异常**不外泄**，且线程继续活着（下一轮照样推）；
  3. `close()` 把积压推完（快 hub 下 pending 清零 + 落 `delivered.json`）；
  4. `close()` **有界**：hub 卡住时按预算收线，不把段末拖成无限等。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_offline_deliver import RUN, _make_artifacts  # type: ignore

from remote.offline_deliver import OfflineDeliverer
from remote.protocol import OFFLINE_ARTIFACT_PATH, OFFLINE_RESULT_PATH


def _wait_until(pred, *, timeout: float = 10.0, step: float = 0.01) -> bool:
    """等一个**事件/状态**成立（`timeout` 只是挂起兜底，不是同步手段，2026-09-24）。

    背景：本文件原先用 `time.sleep(0.2)` 赌「后台线程已经撞上那个异常了」——把线程调度
    延迟当失败（门禁满载时容易红），而且它并不能证明顺序（只是睡够了）。
    """
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


class _Recorder:
    """假 opener：记账全部请求，可注入延迟/故障，返回 200。"""

    def __init__(self, *, delay: float = 0.0, fail_first: int = 0) -> None:
        self.delay = float(delay)
        self.fail_first = int(fail_first)
        self.calls: list[tuple[str, int]] = []
        self.started = time.time()

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        self.calls.append((url, len(data or b"")))
        if self.fail_first > 0:
            self.fail_first -= 1
            raise OSError("boom（假传输异常）")
        if self.delay:
            # sleep-ok: 夹具模拟的工作量：假 hub 的传输耗时
            time.sleep(self.delay)
        return 200, b"{}"

    def posts(self, path: str) -> int:
        return sum(1 for u, _ in self.calls if u.endswith(path))


def _deliverer(root: Path, rec: _Recorder, **kw) -> OfflineDeliverer:
    return OfflineDeliverer(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        background=True,
        opener=rec,  # type: ignore[arg-type]
        log=lambda _m: None,
        **kw,
    )


def test_submit_does_not_wait_for_the_network(tmp_path: Path) -> None:
    """回归：入队必须**立刻**返回（慢 hub 不得给下一轮 PPO 收税）。"""
    root = _make_artifacts(tmp_path)
    rec = _Recorder(delay=0.25)
    d = _deliverer(root, rec)
    d.start()
    try:
        t0 = time.time()
        for it in (1, 2, 3):
            d.submit_round(it)
        queued = time.time() - t0
        assert queued < 0.1, f"入队被网络拖住了 {queued:.3f}s（应只置位即回）"
    finally:
        d.close(timeout=10.0)
    # 后台把三轮回传都推完了（延迟 0.25s/趟 × 每轮 2 趟）
    assert rec.posts(OFFLINE_ARTIFACT_PATH) == 3
    assert d.pending() == []


def test_background_errors_never_reach_the_trainer_and_thread_survives(tmp_path: Path) -> None:
    """后台线程里的传输异常只记一笔：不抛给训练侧，且线程活着继续干活。"""
    root = _make_artifacts(tmp_path, iters=(1, 2))
    rec = _Recorder(fail_first=1)  # 第一次探活就炸（瞬断）——之后必须恢复
    d = _deliverer(root, rec)
    logs: list[str] = []
    d.log = logs.append  # type: ignore[method-assign]
    d.start()
    try:
        d.submit_round(1)
        # 事件驱动（2026-09-24）：等**异常真的发生了**（它一定会写一行日志）再提第二轮——
        # 原来 `time.sleep(0.2)` 只是赌线程已经跑到那里，既不等事件也可能白等。
        assert _wait_until(lambda: bool(logs)), "后台那个异常没留下日志（该响亮不静默）"
        d.submit_round(2)  # 线程若已死，这一轮就再也推不上去
    finally:
        d.close(timeout=10.0)
    assert d.pending() == [], "线程在异常后必须还活着（否则积压永远推不完）"
    assert d.status()["drain_alive"] is False  # close 之后收线了
    assert logs, "异常必须留下日志（响亮，不静默）"


def test_close_flushes_backlog_and_writes_the_ledger(tmp_path: Path) -> None:
    """`close()` 把积压推完：pending 清零 + `delivered.json` 记满（重启续投靠它）。"""
    root = _make_artifacts(tmp_path, iters=tuple(range(1, 9)))
    rec = _Recorder()
    d = _deliverer(root, rec)
    d.start()
    d.submit_round(1)
    d.close(timeout=10.0)
    assert rec.posts(OFFLINE_ARTIFACT_PATH) >= 8, "积压应一次推完（后台 drain 不受 sync_cap 限）"
    assert d.pending() == []
    led = json.loads((root / "delivered.json").read_text(encoding="utf-8"))
    assert sorted(led["artifacts"]) == list(range(1, 9))


def test_close_is_bounded_when_the_hub_hangs(tmp_path: Path) -> None:
    """hub 卡住时 `close()` 按预算收线（产物已在本地，不为旧会话挂死进程）。"""
    root = _make_artifacts(tmp_path, iters=tuple(range(1, 21)))
    rec = _Recorder(delay=0.05)
    d = _deliverer(root, rec)
    d.start()
    t0 = time.time()
    d.close(timeout=0.01)  # 预算 10ms：判据在后台线程自己那一侧
    spent = time.time() - t0
    assert spent < 3.0, f"close 拖了 {spent:.2f}s（有界 flush 失效）"


def test_submit_final_posts_the_segment_result(tmp_path: Path) -> None:
    """段末摘要也会走后台：`submit_final` → drain → `deliver_result`。"""
    root = _make_artifacts(tmp_path, iters=(1, 2))
    rec = _Recorder()
    d = _deliverer(root, rec)
    d.start()
    d.submit_final(it_end=2, state="complete", summary={"last_it": 2})
    d.close(timeout=10.0)
    assert rec.posts(OFFLINE_RESULT_PATH) == 1
    assert d.status()["result_done"] is True


def test_sync_mode_is_still_synchronous(tmp_path: Path) -> None:
    """`background=False`（老行为）仍然是「提交即推」——调用方靠它单步调试/测试。"""
    root = _make_artifacts(tmp_path, iters=(1,))
    rec = _Recorder()
    d = OfflineDeliverer(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        opener=rec,  # type: ignore[arg-type]
        log=lambda _m: None,
    )
    assert d.background is False
    d.submit_round(1)
    assert rec.posts(OFFLINE_ARTIFACT_PATH) == 1, "同步模式下 submit 应当场推完"
    d.close()  # 没起线程 → 空操作

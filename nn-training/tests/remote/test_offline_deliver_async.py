"""tests/remote/test_offline_deliver_async.py —— 补传**与 PPO 并行**（2026-09-22 用户指令）。

缺口：`OfflineDeliverer` 原先只有同步 `sync()`，而 `run_loop._checkpoint()` 是**内联**调的
——每轮落盘后最多 4 趟 POST（各 ~1.9MB、60s 超时）跑在下一轮 PPO 之前。回传与训练没有
任何数据依赖，那份等待纯属白花。

现在：训练线程只 `submit_round()` 入队（非阻塞），一个 `offline-deliver` daemon 线程独自
拥有补传状态（单写者），段末 `close(timeout)` 做**有界** flush。本文件钉四件事：

  1. `submit_round()` **不等网络**（慢 hub 下入队耗时与 POST 时长无关）；
  2. 后台线程里的任何异常**不外泄**，且线程继续活着（下一轮照样推）；
  3. `close()` 把积压推完（快 hub 下 pending 清零 + 落 `delivered.json`）；
  4. `close()` **有界**：hub 卡住时按预算收线，不把段末拖成无限等。

2026-10-07 补（`plan/offline-deliver-isolation.plan.md` §2 / §10，2026-10-06 事故）：

  5. **不许空转**：`_repost` 是唯一「只唤醒、不消费」的标志 —— 探活失败把它放回后，外层循环
     既不 `wait` 也不干活，退化成 100% 核自旋（忙等期间这条腿**一个日志都不打**）。判据 =
     **不变量探针** `_idle_spins`（只在违反时 +1 ⇒ 断言 `== 0`，确定性、无窗口/容差）；
  6. **重投不许丢**：探活失败要能重试、段末只有 repost（backlog 空）也要送、送不掉要**响亮**；
     而重投是**磁盘状态**（`delivered.json` 的 `owed_reposts`）—— 那一轮早已记账，`pending()`
     不会再含它，丢了就是 hub 侧该轮 `eval_rows` **永久缺失**。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import OFFLINE_ARTIFACT_PATH, OFFLINE_RESULT_PATH
from remote.deliver_proc import DelivererProcess, make_deliverer
from remote.offline_deliver import OfflineDeliverer
from tests.remote.test_offline_deliver import RUN, _make_artifacts  # type: ignore


class _FakeProc:
    """假子进程：拉起面用（argv/env/cwd/退出码）——**不起真进程**。

    为什么要它：真起子进程的用例是慢层（`test_offline_deliver_proc.py`），而「argv 里不许有
    token」这类断言只需要看**拉起那一刻**的形状。八次 python 启动会把同一台机器上的邻居用例
    （0.3s 级看门狗）顶红，所以能不起就不起（2026-10-07 实测）。
    """

    def __init__(self, boot: bool = True) -> None:
        self.stdout = StringIO("boot ok 1\n" if boot else "")
        self.stderr = StringIO("")
        self.stdin = BytesIO()
        self.pid = 1
        self._code: int | None = None

    def poll(self) -> int | None:
        return self._code

    def wait(self, timeout: float | None = None) -> int:
        self._code = 0
        return 0

    def terminate(self) -> None:
        self._code = -15

    def kill(self) -> None:
        self._code = -9


def _seed_ledger(
    root: Path, *, artifacts: tuple[int, ...] = (), result_done: bool = False
) -> None:
    """手搓 `delivered.json`（「这几轮已经投过」是走重投那条路的前提）。"""
    (root / "delivered.json").write_text(
        json.dumps(
            {"run_id": RUN, "artifacts": list(artifacts), "result_done": bool(result_done)}
        ),
        encoding="utf-8",
    )


def _ledger(root: Path) -> dict:
    data = json.loads((root / "delivered.json").read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _dead_530(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """假 opener：一律 530（隧道断线形态；注意 530 **不是** 401/403 ⇒ 不停用整条腿）。"""
    return 530, b""


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
        #: 原始体（按 URL 留存；T6 的断言要看 `offline/result` 体的 `end_it_reached`）。
        self.bodies: list[tuple[str, bytes]] = []

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        self.calls.append((url, len(data or b"")))
        self.bodies.append((url, data or b""))
        if self.fail_first > 0:
            self.fail_first -= 1
            raise OSError("boom（假传输异常）")
        if self.delay:
            # sleep-ok: 夹具模拟的工作量：假 hub 的传输耗时
            time.sleep(self.delay)
        return 200, b"{}"

    def posts(self, path: str) -> int:
        return sum(1 for u, _ in self.calls if u.endswith(path))

    def body_for(self, path: str) -> dict:
        for u, raw in self.bodies:
            if not u.endswith(path):
                continue
            try:
                got = json.loads(raw.decode("utf-8"))
            except ValueError:
                return {}
            return got if isinstance(got, dict) else {}
        return {}


def _deliverer(
    root: Path, rec: Callable[[str, bytes, dict, float], tuple[int, bytes]], **kw
) -> OfflineDeliverer:
    return OfflineDeliverer(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        background=True,
        opener=rec,
        log=lambda _m: None,
        **kw,
    )


def test_submit_does_not_wait_for_the_network(tmp_path: Path) -> None:
    """回归：入队必须**立刻**返回（慢 hub 不得给下一轮 PPO 收税）。"""
    # delay 0.25 → 0.1（2026-09-29，§43）：判据是「入队不阻塞」（`queued < 0.1`），而假 hub 的
    # 传输耗时只决定后台要排多久 —— 0.1s × 每轮 2 趟 = 0.2s/轮，**如果**入队真被网络拖住，
    # 单轮就超过 0.1s 阀值（守卫不会变瞎），而本用例的墙钟跟着降一半。
    root = _make_artifacts(tmp_path)
    rec = _Recorder(delay=0.1)
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
    # 后台把三轮回传都推完了（延迟 0.1s/趟 × 每轮 2 趟）
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
    d.submit_final(it_end=2, state="complete", summary={"last_it": 2}, end_it_reached=True)
    d.close(timeout=10.0)
    assert rec.posts(OFFLINE_RESULT_PATH) == 1
    assert d.status()["result_done"] is True
    # T6：`end_it_reached` 必须穿过后台队列（tuple 解包漏一格就会静默丢标志）
    assert rec.body_for(OFFLINE_RESULT_PATH)["end_it_reached"] is True


def test_drain_loop_never_spins_without_waiting_or_working(tmp_path: Path) -> None:
    """2026-10-06 事故回归：530（探活失败）+ 待重投 ⇒ 禁止「既不干活也不 wait」的圈。

    事故形态：`_repost` 非空、`_want_sync`/`_final` 为假时，`_drain_loop` 的内层等待条件
    认它（于是不进 `wait`），而 work 分支不认它（于是不干活）⇒ 纯转圈烧一个核。
    判据是**不变量探针**（不是 CPU 时间/墙钟阈值——那是 `test_no_sleep_as_sync` 要拦的那类）。
    """
    root = _make_artifacts(tmp_path)
    rec = _Recorder()
    d = OfflineDeliverer(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        background=True,
        opener=_dead_530,  # type: ignore[arg-type]
        log=lambda _m: None,
    )
    d.start()
    try:
        d.submit_eval_round(1)  # 登记重投 + 唤醒（旧实现此后进自旋）
        # sleep-ok: 轮询步长（等的是「探针是否被触发」这个状态，超时只当挂起兜底）
        _wait_until(lambda: d._idle_spins > 0, timeout=0.6)
        assert d._idle_spins == 0, (
            f"补传线程空转了 {d._idle_spins} 圈（既不干活也不 wait）——"
            "不变量：每一次唤醒，要么干活，要么 wait"
        )
        assert rec.calls == [], "530 时不该有任何请求被当成投递"
    finally:
        d.close(timeout=0.1)


def test_repost_is_retried_after_the_probe_recovers(tmp_path: Path) -> None:
    """修忙等不得把 `_repost` 弄丢：探活先失败、之后恢复 ⇒ 重投仍送达（§2.3 绿回归）。"""
    root = _make_artifacts(tmp_path, iters=(1,))
    _seed_ledger(root, artifacts=(1,))  # 这一轮「已投递」⇒ pending 空，只有重投一条路
    rec = _Recorder(fail_first=1)  # 第一次探活炸（瞬断）
    d = _deliverer(root, rec, probe_ttl=0.0)  # TTL=0 ⇒ 每次唤醒都重探（不赌 60s 缓存）
    d.start()
    try:
        d.submit_eval_round(1)  # 这一次探活失败 ⇒ 重投留着
        # sleep-ok: 轮询步长（等的是「探活真的失败过一次」这个状态）
        assert _wait_until(lambda: len(rec.calls) >= 1, timeout=2.0)
        assert d.unsent_reposts() == [1], "探活失败后重投必须留在集合里（不是丢掉）"
        d.submit_round(1)  # 下一轮唤醒（真实里由下一轮 submit_round 承担）
        # sleep-ok: 轮询步长（等的是「重投被送达」这个状态）
        assert _wait_until(lambda: rec.posts(OFFLINE_ARTIFACT_PATH) == 1, timeout=5.0), (
            "探活恢复后重投没被送到"
        )
        assert d.unsent_reposts() == [], "送达后必须销账"
    finally:
        d.close(timeout=2.0)


def test_close_delivers_a_repost_when_the_backlog_is_empty(tmp_path: Path) -> None:
    """段末只有 repost、backlog 为空 ⇒ 旧 `close()` 唤醒条件不含 `_repost`，直接把它丢了。"""
    root = _make_artifacts(tmp_path, iters=(1,))
    _seed_ledger(root, artifacts=(1,))
    rec = _Recorder(fail_first=1)  # 让重投先失败一次（负结果被 TTL 缓存）
    d = _deliverer(root, rec)
    d.start()
    try:
        d.submit_eval_round(1)
        # sleep-ok: 轮询步长（等的是「重投试过并留下」这个状态）
        assert _wait_until(lambda: d.unsent_reposts() == [1] and bool(rec.calls), timeout=2.0)
        d.close(timeout=5.0)  # 收线时强制探一次（绕过负结果 TTL）⇒ 这一趟必须把它送出去
    finally:
        pass
    assert rec.posts(OFFLINE_ARTIFACT_PATH) == 1, "段末只剩重投时不许丢（旧代码在这里丢）"
    assert d.pending() == []
    assert _ledger(root)["owed_reposts"] == [], "送达即销账（磁盘状态要跟上）"


def test_unsent_repost_is_loud_and_persisted(tmp_path: Path) -> None:
    """送不掉的重投：响亮一行 + 落 `owed_reposts`（磁盘状态 = 下次会话能续投）。"""
    root = _make_artifacts(tmp_path, iters=(1,))
    _seed_ledger(root, artifacts=(1,))
    logs: list[str] = []
    d = OfflineDeliverer(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        background=True,
        opener=_dead_530,  # type: ignore[arg-type]
        log=logs.append,
    )
    d.start()
    try:
        d.submit_eval_round(1)
        # sleep-ok: 轮询步长（等的是「重投已登记」这个状态）
        assert _wait_until(lambda: d.unsent_reposts() == [1], timeout=2.0)
        d.close(timeout=0.1)
    finally:
        pass
    assert any("重投" in m and "未送达" in m for m in logs), f"段末必须响亮：{logs}"
    assert d.status()["reposts_unsent"] == 1
    assert _ledger(root)["owed_reposts"] == [1], "欠账必须落盘（否则下次会话无从续投）"


def test_owed_reposts_are_resent_by_the_next_session(tmp_path: Path) -> None:
    """跨会话续投：上一段没送出去的重投，新会话（同一产物目录）自动补上。

    为什么必须落盘：那一轮**早已在 `delivered.json` 记账** ⇒ `pending()`（磁盘有−账本无）永远
    不会再含它 ⇒ 丢一次就是 hub 侧该轮 `eval_rows` 永久缺失（`submit_eval_round` 存在的唯一理由）。
    """
    root = _make_artifacts(tmp_path, iters=(1,))
    _seed_ledger(root, artifacts=(1,))
    dead = _deliverer(root, _dead_530)  # 上个会话：全程 530（重投送不出去）
    dead.start()
    try:
        dead.submit_eval_round(1)
        # sleep-ok: 轮询步长（等的是「重投已登记」这个状态）
        assert _wait_until(lambda: dead.unsent_reposts() == [1], timeout=2.0)
        dead.close(timeout=0.1)
    finally:
        pass
    assert _ledger(root)["owed_reposts"] == [1]
    rec = _Recorder()  # 新会话：hub 活了
    live = _deliverer(root, rec)
    live.start()
    live.close(timeout=5.0)  # `close()` 的唤醒条件含 `_repost` ⇒ 欠账这一趟就该清掉
    assert rec.posts(OFFLINE_ARTIFACT_PATH) == 1, "上一段欠下的重投必须被新会话补上"
    assert _ledger(root)["owed_reposts"] == []


def test_process_mode_passes_the_token_by_env_never_by_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★拉起面的形状：token 只走 `$BATTLE_HUB_TOKEN`（argv 会在 `/proc/<pid>/cmdline` 上全机可读）。

    「真的带上了鉴权头」由慢层证（`test_offline_deliver_proc.py` 的假 hub 对错 token 回 403、
    而 403 = 停用整条腿）——那里真起子进程；这里只看拉起那一刻的 argv/env。
    """
    seen: dict = {}

    def fake_popen(argv, *a, **kw):  # type: ignore[no-untyped-def]
        seen["argv"] = [str(x) for x in argv]
        seen["env"] = dict(kw.get("env") or {})
        seen["cwd"] = str(kw.get("cwd") or "")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    d = DelivererProcess(
        base_url="http://127.0.0.1:1",
        token="sekret-tok",
        run_id=RUN,
        artifacts_dir=tmp_path / "art",
        work_dir=tmp_path / "work",
        log=lambda _m: None,
    )
    d.start()
    try:
        assert seen, "没走到 Popen（进程模式没起来）"
        assert "sekret-tok" not in " ".join(seen["argv"]), "token 出现在 argv 里了"
        assert seen["env"].get("BATTLE_HUB_TOKEN") == "sekret-tok", "token 必须显式塞进子进程 env"
        assert seen["env"].get("PYTHONPATH"), "PYTHONPATH 不继承 ⇒ 云机上会 No module named"
        assert "-m" in seen["argv"] and "remote.deliver_worker" in seen["argv"]
        assert seen["cwd"].replace("\\", "/").endswith("nn-training"), f"cwd 不对：{seen['cwd']}"
        # 控制通道：`--ctl` 落点就是会话文件，命令以 JSONL append 进去（父→子的唯一入口）
        ctl = Path(str(d.status()["ctl"]))
        d.submit_round(7)
        assert json.loads(ctl.read_text(encoding="utf-8").strip().splitlines()[-1]) == {"round": 7}
        assert d.status()["mode"] == "process" and d.status()["drain_alive"] is True
    finally:
        d.close(timeout=0.0)
    assert json.loads(ctl.read_text(encoding="utf-8").strip().splitlines()[-1]) == {
        "stop": True,
        "budget": 0.0,
    }


def test_make_deliverer_defaults_to_process_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工厂的缺省模式 = 独立子进程（离线路径的缺省；测试/单步调试才显式给别的）。

    单测全局钉着 `NN_DELIVER_MODE=thread`（`tests/conftest.py::pin_production_env`：单测不为
    补传起真子进程），所以这里先把那个桩拆掉 —— 顺带钉住解析顺序「参数 > env > 缺省」。
    """
    monkeypatch.delenv("NN_DELIVER_MODE", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProc())
    d = make_deliverer(
        hub_url="http://127.0.0.1:1",
        hub_token="tok",
        run_id=RUN,
        artifacts_dir=_make_artifacts(tmp_path / "a"),
        work_dir=tmp_path / "work",
        log=lambda _m: None,
    )
    assert d is not None and isinstance(d, DelivererProcess), "离线路径的缺省必须是进程模式"
    d.start()
    assert d.status()["mode"] == "process"
    d.close(timeout=0.0)


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


def test_restart_restating_the_final_does_not_self_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★C1+C15 的重启路径会在 `_append` **锁内**再调一次 `_append`（重述段末摘要）——
    锁若不可重入 ⇒ 同一线程自锁死 ⇒ `submit_round()` 永不返回（训练线程永挂）。

    2026-10-09 门禁在 8 worker 满载下真踩到：慢层 `test_restart_does_not_replay_consumed_commands`
    60s 超时，转储栈停在 `remote/deliver_proc.py::_append` 的 `with self._lock:`。
    触发窗口 = kill 子进程时 hub 已收到 POST、而子进程还没把 `result_done` 落盘。

    判据是**有界返回**（自锁死是「不返回」不是「抛错」）：起一条线程调 `_append`，主线程等 5s；
    没返回就不收线（收线也要拿那把锁，会跟着一起挂死），直接判失败。
    """
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProc())
    d = DelivererProcess(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=tmp_path / "art",
        work_dir=tmp_path / "work",
        log=lambda _m: None,
    )
    d.start()
    ctl = Path(str(d.status()["ctl"]))
    d.submit_final(it_end=1, state="complete", summary={}, end_it_reached=True)
    assert d._proc is not None
    d._proc.kill()  # 子进程「崩了」（OOM / 被平台杀）
    # `result_done` 还没落的窗口（hub 收到 POST ≠ 子进程已记账）⇒ 重启后要重述 final
    monkeypatch.setattr(d, "_result_done_from_disk", lambda: False)
    done = threading.Event()

    def call() -> None:
        d.submit_round(2)
        done.set()

    t = threading.Thread(target=call, daemon=True, name="append-under-test")
    t.start()
    ok = done.wait(5.0)
    # 先读盘再收线：`close` 自己也会 `_append`（stop 行），而它同样要拿那把锁
    lines = [json.loads(x) for x in ctl.read_text(encoding="utf-8").splitlines() if x.strip()]
    if ok:
        d.close(timeout=0.0)  # 没自锁死才敢收线（挂死的线程正持着锁）
    assert ok, "`_append` 自锁死（同一线程重入不可重入锁）——训练线程会永挂"
    assert lines[-1] == {"round": 2}, lines
    assert sum(1 for x in lines if "final" in x) == 2, f"重启后的重述要再落一行 final：{lines}"


def test_result_done_prefers_the_ledger_over_a_stale_status_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重启时判「段末摘要送没送」的证据优先级：**账本 > 状态面快照**。

    两份证据的写入时刻差一整拍 —— 账本是子进程拿到 200 后**当即** `_save_ledger()` 写的那份，
    而状态面是**每 tick 的快照**（`DRAIN_TICK_SEC = 0.5` 再加一趟 sync）。先看快照 ⇒ 在
    「hub 已收到 POST、快照还没刷新」这一段里判 False ⇒ 白重述一次段末摘要（hub 侧覆盖写，
    不丢数据，但白付一次 ~1.9MB 的 POST，且 `close()` 会虚报「段末摘要未送达」）。

    2026-10-10：这条优先级是查慢层 `test_restart_does_not_replay_consumed_commands` 的满载
    flake 时量出来的（探针实测：窗口里**两边都还没落** ⇒ 那次重述是设计行为，见该用例与
    `docs/nn/remote-transport.md` §77）。
    """
    root = _make_artifacts(tmp_path / "art")
    _seed_ledger(root, artifacts=(1,), result_done=True)  # 子进程拿到 200 后当即写的
    d = DelivererProcess(
        base_url="http://127.0.0.1:1",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        work_dir=tmp_path / "work",
        log=lambda _m: None,
    )
    # 状态面：**陈旧快照**（上一个 tick 写的，说没送）—— 0.5s 的窗口就在这
    monkeypatch.setattr(d, "_child_status", lambda: {"pid": 4321, "result_done": False})
    assert d._result_done_from_disk() is True, (
        "账本说送了、快照还没刷新 ⇒ 必须信账本（否则重启白重述一次 ~1.9MB 的段末摘要）"
    )

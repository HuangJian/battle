"""tests/remote/test_offline_deliver.py —— 产物**补传**（「中途能连上 hub 就自动恢复在线回传」）。

需求（用户 2026-09-17 后半句）：任务包搬上云机后自主跑完，产物以 Kaggle/Colab 官方方式
打包下载；**如果训练中途发现可以联通 hub，云机也能自动恢复产物在线回传**。

补传的全部价值都建立在一条不变量上：**它不能影响训练**。因此本文件把三类断言钉死：

  1. **通了就有**：每轮落盘后 hub 侧出现 `offline/<run_id>/it-NNN/{weights.json,opt.tar}`、
     逐轮账本行、段末摘要 —— 而且 hub 的 **job 池完全不被污染**（补传不是 job：没有
     job_pending、没有租约、没人会来领）。
  2. **不通就等于没做**（这是最容易做坏的一侧）：hub 关机 / 端口没人听 / token 错
     —— `sync()` 返回 0、**不抛异常**、不标记已投递；训练侧一行日志都没有后果。
  3. **重来不重传、积压会补上**：`delivered.json` 是**唯一**的记账（重启/新会话靠它续投），
     已投递的轮次不再推；第一次连上时，之前攒的轮次一次补齐。

还要钉住两个「不这样写就会静默出事」的判定：
  * **坏 token 只试一次**（D9 闭锁是「同 IP 五次无效鉴权封 3600s」，每轮重试等于自己把自己
    封掉；而且配置错重试一百次也不会对）；
  * **体被拒（400/413）也不再试**（体是自己造的，形状不对 = 版本不匹配，重试无意义）。

以及 hub 侧对**不可信输入**的边界：`run_id` 是目录名（`../` 就是任意文件写）、权重指纹
与实收字节必须相符（否则一条损坏的权重会以「hub 上的产物」身份进入 eval/续跑）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_DELIVERED_NAME,
    OFFLINE_RESULT_PATH,
    ProtocolError,
    encode_weights_json,
    sanitize_run_id,
)
from remote.offline_deliver import (
    DISABLE_COOLDOWN_SEC,
    MAX_FAILS_PER_PASS,
    PROBE_FAIL_TTL_SEC,
    PROBE_TTL_SEC,
    REJECTED_MAX_TRIES,
    REJECTED_RESET_KEY,
    REJECTED_TTL_SEC,
    OfflineDeliverer,
    reset_rejected,
)
from remote.offline_deliver import main as deliver_main
from remote.run_loop import run_plan_job
from tests.remote.test_remote_ppo import _boot_server, _http  # type: ignore
from tests.remote.test_run_loop import _FakeRunJob, _prepare, _quiet  # type: ignore


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把**权重归档根**指到 tmp（2026-09-23）：回传轮会往它写 `<课>.it<N>.<时间戳>.json`
    （用户口径的 ③），工装用例不得往真 `nn-training/weights/` 撒文件 —— 那些会被控制台
    的 evalA 权重选择器当成真训练轮次列出来。"""
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


RUN = "run-offline-1"


class _RefusingHub:
    """`common.net_http.urlopen` 的替身：`down=True` 时抛「连接被拒」（= hub 关机的真实形状）。

    **为什么不用 `http://127.0.0.1:1` 这个真死端口**（2026-10-20）：那个端口没人听**不等于**
    连上去就立刻被拒 —— 防火墙/沙箱把 SYN 丢掉时 `connect` 会一路挂到 `probe()` 自己的
    20s 上限。于是 `test_hub_unreachable_…`（两次探活）白等 40s、
    `test_run_plan_job_ignores_delivery_failures`（三轮）白等 60s，两条都被耗时预算
    （>30s）判红，而断言的**语义**一条都没变。

    语义等价：`ConnectionRefusedError` 正是 `_urllib_opener` 在生产里抛出的那一种，
    `probe()` 的 `except Exception` 分支逐字相同（两者都落到「不可达」）。
    `down` 可以翻回 False ⇒ 同一个用例还能演「hub 又回来了」，而这时它委托给**真**的
    `net_http.urlopen`（回环照旧绕开代理）。
    """

    def __init__(self, real) -> None:
        self._real = real
        self.down = True
        #: 每一次被拦下的 URL（断言「真发过探活」用——否则「不可达」可能只是没发）。
        self.calls: list[str] = []

    def __call__(self, req, timeout: float = 30.0):
        url = getattr(req, "full_url", None) or str(req)
        self.calls.append(url)
        if self.down:
            raise ConnectionRefusedError(111, "Connection refused")
        return self._real(req, timeout=timeout)


@pytest.fixture
def hub_down(monkeypatch: pytest.MonkeyPatch) -> _RefusingHub:
    """把回环 HTTP 换成「hub 关机」（见 `_RefusingHub`；不打桩就白等 20s/发）。"""
    from common import net_http

    stub = _RefusingHub(net_http.urlopen)
    monkeypatch.setattr(net_http, "urlopen", stub)
    return stub


def _make_artifacts(root: Path, *, run_id: str = RUN, iters: tuple[int, ...] = (1, 2, 3)) -> Path:
    """手搓一个产物目录（形状与 `ArtifactStore` 同规：manifest + it-NNN/ + metrics.jsonl）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "plan_sha256": "p" * 64,
                "course_fp": "c" * 64,
                "commit": "d" * 40,
            }
        ),
        encoding="utf-8",
    )
    rows = []
    for it in iters:
        d = root / f"it-{it:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "weights.json").write_bytes(json.dumps({"it": it, "w": it * 1.5}).encode("utf-8"))
        (d / "opt.tar").write_bytes(b"opt-%d" % it)
        rows.append(json.dumps({"it": it, "wall_sec": 1.0, "agg": {"kl": 0.01}}))
    (root / "metrics.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def _add_round(root: Path, it: int) -> None:
    """给已有产物目录**追加**一轮（目录 + 账本行 + opt，形状与 `ArtifactStore.checkpoint` 同规）。"""
    d = root / f"it-{it:03d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "weights.json").write_bytes(json.dumps({"it": it, "w": it * 1.5}).encode("utf-8"))
    (d / "opt.tar").write_bytes(b"opt-%d" % it)
    with (root / "metrics.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"it": it, "wall_sec": 1.0, "agg": {"kl": 0.01}}) + "\n")


def _rewrite_round(root: Path, it: int, *, w: float) -> None:
    """换掉某一轮的权重字节（模拟「本机这一轮被重跑过」），**不动账本**（否则两条判据混在一起）。"""
    (root / f"it-{it:03d}" / "weights.json").write_bytes(json.dumps({"it": it, "w": w}).encode("utf-8"))


def _write_eval_log(root: Path, *, it: int = 1, games: int = 2, salt: str = "") -> Path:
    """写一份 `eval_log.jsonl`（行取自**唯一**构造点 `worker.eval_rows.eval_row`，不手抄 schema）。"""
    from worker.eval_rows import eval_row

    rows = [
        eval_row(
            {"dims": {}, "win": True, "cleared": True, "score": 100, "kills": 4},
            it=it,
            key16=(salt or "k") * 16,
            task=(1, 100 + i),
            node="cloud",
        )
        for i in range(games)
    ]
    p = root / "eval_log.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def _write_ledger(
    root: Path,
    *,
    artifacts: list[int] | None = None,
    rejected: list[dict] | None = None,
    run_id: str = RUN,
) -> Path:
    """手写一份 `delivered.json`（跨会话那两笔欠账就是从这里读回来的）。"""
    p = root / OFFLINE_DELIVERED_NAME
    p.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "hub_url": "http://hub.invalid",
                "artifacts": artifacts or [],
                "owed_reposts": [],
                "rejected": rejected or [],
                "updated_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    return p


def _weights_fp(root: Path, it: int) -> str:
    import hashlib

    return hashlib.sha256((root / f"it-{it:03d}" / "weights.json").read_bytes()).hexdigest()


def _deliverer(base: str, root: Path, *, token: str = "sekret", **kw: object) -> OfflineDeliverer:
    return OfflineDeliverer(
        base_url=base, token=token, run_id=RUN, artifacts_dir=root, log=_quiet, **kw  # type: ignore[arg-type]
    )


def _artifact_body(root: Path, it: int, *, run_id: str = RUN, fp: str = "") -> dict:
    wj = (root / f"it-{it:03d}" / "weights.json").read_bytes()
    import hashlib

    return {
        "run_id": run_id,
        "it": it,
        "weights_fp": fp or hashlib.sha256(wj).hexdigest(),
        "weights_json": encode_weights_json(wj),
        "opt_tar_b64": "",
        "row": {"it": it},
    }


# ────────────────────────── 通了就有（真 hub，真端点） ──────────────────────────


def test_sync_pushes_backlog_lands_on_hub_and_does_not_touch_job_pool(tmp_path: Path) -> None:
    """每轮产物落到 `<job_root>/offline/<run>/it-NNN/`，且 **job 池一无所知**。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        d = _deliverer(base, root)
        assert d.sync() == 3
        run_dir = store.offline_run_dir(RUN)
        for it in (1, 2, 3):
            got = (run_dir / f"it-{it:03d}" / "weights.json").read_bytes()
            assert got == (root / f"it-{it:03d}" / "weights.json").read_bytes()
            assert (run_dir / f"it-{it:03d}" / "opt.tar").exists()
        # 逐轮账本行（一行一轮）+ run.json 血缘
        lines = [
            json.loads(ln)
            for ln in (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert [ln["it"] for ln in lines] == [1, 2, 3]
        assert all(ln["event"] == "offline_artifact" for ln in lines)
        assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["plan_sha256"] == "p" * 64
        # 补传不是 job：没有待领池条目、没有 job_pending 账本行（否则控制台会显示一条
        # 永远等不到工人的任务，还可能被别的节点领去做错事）。
        assert store.claimable_job_ids() == []
        assert not (store.jsonl_path).exists() or "job_pending" not in store.jsonl_path.read_text(
            encoding="utf-8"
        )
        # 幂等：再 sync 一次无事可做
        assert d.sync() == 0
    finally:
        srv.shutdown()
        th.join(timeout=5)


# ───────────── 备份抽屉（拍板-3，`plan/offline-local-disk-first` §3.4 C1）─────────────


def test_backup_writes_pending_rounds_and_is_idempotent(tmp_path: Path) -> None:
    """待投轮次 ⇒ 进抽屉（轮次目录 + 锚点/全量包），且**重跑不重写**（幂等）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)  # base 空 ⇒ 推不出去 ⇒ 三全都 pending
    assert d.backup_pending() == 3
    for it in (1, 2, 3):
        assert (drawer / f"it-{it:03d}" / "weights.json").is_file()
    assert (drawer / "manifest.json").is_file(), "锚点（抽屉自己就是一份能反向恢复的产物）"
    # 幂等：再跑一遍不重复写（拿 mtime 当「写没写」的判据）
    before = (drawer / "it-001" / "weights.json").stat().st_mtime_ns
    assert d.backup_pending() == 0
    assert (drawer / "it-001" / "weights.json").stat().st_mtime_ns == before


def test_backup_rewrites_a_round_whose_bytes_changed(tmp_path: Path) -> None:
    """★ C3 对账：抽屉里那一轮与本机**字节不符** ⇒ 重写（幂等只对「一致」那一种成立）。

    §3.4 的原话是「已存在**且对账一致** ⇒ 跳过」：只判「存在」会把「本机这一轮被重跑过」的
    旧字节永久留在抽屉里，而恢复腿会把它当成「这一轮」铺回去——看起来完全正常。
    两件对账（`weights.json` 与 `opt.tar`）各自都算数。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 2
    stale = (drawer / "it-002" / "weights.json").read_bytes()
    # 本机第二轮被重跑（同一个 it、不同字节）：权重与 opt 都换掉
    (root / "it-002" / "weights.json").write_bytes(json.dumps({"it": 2, "w": 99.0}).encode("utf-8"))
    (root / "it-002" / "opt.tar").write_bytes(b"opt-2-new")
    assert d.backup_pending() == 1, "只该重写对不上的那一轮"
    now = (drawer / "it-002" / "weights.json").read_bytes()
    assert now == (root / "it-002" / "weights.json").read_bytes() and now != stale
    assert (drawer / "it-002" / "opt.tar").read_bytes() == b"opt-2-new"
    # 再跑一遍：字节已一致 ⇒ 不再重写
    before = (drawer / "it-002" / "weights.json").stat().st_mtime_ns
    assert d.backup_pending() == 0
    assert (drawer / "it-002" / "weights.json").stat().st_mtime_ns == before
    # 只动 `opt.tar`（权重没变）⇒ 也算对不上：Adam 动量的字节是恢复后接着跑的前提
    (root / "it-002" / "opt.tar").write_bytes(b"opt-2-newer")
    assert d.backup_pending() == 1
    assert (drawer / "it-002" / "opt.tar").read_bytes() == b"opt-2-newer"


def test_backup_writes_nothing_when_everything_was_delivered(tmp_path: Path) -> None:
    """★ DoD-6（反探针）：补传全成功的一段 ⇒ 网络侧抽屉**连目录都不建**（常态零写入）。"""
    base, _store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        drawer = tmp_path / "net" / "backup"
        d = _deliverer(base, root, backup_root=drawer)
        assert d.sync() == 3  # 全推上去 ⇒ pending 空
        assert d.backup_pending() == 0
        assert not drawer.exists(), "全成功的一段不该碰网络盘"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_backup_failure_is_only_a_warning(tmp_path: Path) -> None:
    """★ DoD-8 的一半：抽屉建不出来（父路径是个文件）⇒ 只记一笔、**不抛**（备份是第二份拷贝）。"""
    root = _make_artifacts(tmp_path / "art")
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"x")
    logs: list[str] = []
    d = OfflineDeliverer(
        base_url="", token="", run_id=RUN, artifacts_dir=root, backup_root=blocker / "backup",
        log=logs.append,
    )
    assert d.backup_pending() == 0
    assert any("备份跳过" in m for m in logs), logs


def test_no_drawer_configured_means_no_backup(tmp_path: Path) -> None:
    """没给 `backup_root`（纯离线腿 / 老调用方）⇒ 这个函数是空操作。"""
    root = _make_artifacts(tmp_path / "art")
    assert _deliverer("", root).backup_pending() == 0
    assert list(tmp_path.glob("**/backup")) == []


def test_delivered_ledger_makes_redelivery_restart_safe(tmp_path: Path) -> None:
    """`delivered.json` 是唯一记账：**新会话**带着同一个目录接上时不重传已投递的轮次。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
        assert _deliverer(base, root).sync() == 3
        # 第 4 轮是新落盘的 → 新会话只补它
        _make_artifacts(root, iters=(4,))
        d2 = _deliverer(base, root)
        assert d2.pending() == [4]
        assert d2.sync() == 1
        assert (store.offline_run_dir(RUN) / "it-004" / "weights.json").exists()
        assert _deliverer(base, root).pending() == []
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_hub_unreachable_is_free_and_backlog_delivers_on_reconnect(
    tmp_path: Path, hub_down: _RefusingHub
) -> None:
    """**核心需求**：hub 关机期间照跑（返回 0、不抛、不标记）；连上那天积压一次补齐。"""
    root = _make_artifacts(tmp_path / "art")
    down = _deliverer("http://127.0.0.1:1", root)  # 没人听的端口
    assert down.sync() == 0  # 不抛
    assert hub_down.calls and hub_down.calls[0].endswith("/ping"), (
        "一次探活都没发出去 —— 「不可达」不是被测出来的"
    )
    assert not (root / "delivered.json").exists()  # 什么都没记成已投递
    assert down.pending() == [1, 2, 3]
    assert down.deliver_result(it_end=3, state="complete") is False

    # 中途连上了（同一目录、同一个 run）：放行真回环，让下面刚起的 server 能被连上
    hub_down.down = False
    base, store, srv, th = _boot_server(tmp_path)
    try:
        up = _deliverer(base, root)
        assert up.sync() == 3  # 积压补齐
        assert (
            up.deliver_result(
                it_end=3, state="complete", summary={"rows": 3}, end_it_reached=True
            )
            is True
        )
        rec = json.loads((store.offline_run_dir(RUN) / "result.json").read_text(encoding="utf-8"))
        assert rec["it_end"] == 3 and rec["state"] == "complete"
        assert rec["end_it_reached"] is True
    finally:
        srv.shutdown()
        th.join(timeout=5)


class _CountingOpener:
    """记录调用次数并返回固定响应的替身 opener（用来断言「不再试第二次」）。"""

    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status = status
        self.body = body
        self.calls: list[str] = []

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
        self.calls.append(url)
        return self.status, self.body


def test_bad_token_disables_delivery_after_one_attempt(tmp_path: Path) -> None:
    """401/403 = 配置问题（且 D9 会把每轮重试的自己封掉）⇒ 试一次就停用，不重试。"""
    root = _make_artifacts(tmp_path / "art")
    op = _CountingOpener(401, b'{"error":"unauthorized"}')
    d = _deliverer("http://hub.invalid", root, opener=op)
    assert d.sync() == 0
    assert d.disabled_reason != ""
    assert len(op.calls) == 1  # 只探了那一次 /ping
    assert d.sync() == 0
    assert d.deliver_result(it_end=3, state="complete") is False
    assert len(op.calls) == 1  # 停用后一次网都不出
    assert d.pending() == [1, 2, 3]  # 一轮都没标记成已投递
    assert not (root / "delivered.json").exists()


def test_failed_probe_is_retried_after_the_short_ttl(tmp_path: Path) -> None:
    """探活**失败**只缓存 `PROBE_FAIL_TTL_SEC`（不是 60s）⇒ 隧道刚回来不必等满一分钟才补投。

    评审 F2 / plan §4.2-1：本片要的是「探活失败负缓存收紧、封顶 ≤60s」，不是年级退避。
    """
    root = _make_artifacts(tmp_path / "art")
    clock = [1000.0]
    op = _CountingOpener(503, b"{}")
    d = _deliverer("http://hub.invalid", root, opener=op, now_fn=lambda: clock[0])
    assert d.sync() == 0
    held = len(op.calls)
    clock[0] += PROBE_FAIL_TTL_SEC - 1.0
    assert d.sync() == 0
    assert len(op.calls) == held, "失败负缓存窗内不再出门"
    clock[0] += 2.0
    assert d.sync() == 0
    assert len(op.calls) == held + 1, f"过了 {PROBE_FAIL_TTL_SEC:g}s 就重探（不是 60s）"


def test_disabled_delivery_revives_after_the_cooldown(tmp_path: Path) -> None:
    """C1（评审 F1）：401 停用**不是死刑**——冷却到点自动解封并真补投。

    现场形状：一条腿 401 之后本会话零尝试，而同期打点能拿到 200（⇒ 不是链路全断）。
    冷却窗内仍然一次网都不出（D9 闭锁的口径不变），到点后 token 已轮换就应当恢复。
    """
    root = _make_artifacts(tmp_path / "art")
    clock = [1000.0]
    box = {"status": 401}

    class _Op:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
            self.calls.append(url)
            return box["status"], b"{}"

    op = _Op()
    msgs: list[str] = []
    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="tok",
        run_id=RUN,
        artifacts_dir=root,
        log=msgs.append,
        opener=op,
        now_fn=lambda: clock[0],
    )
    assert d.sync() == 0
    assert d.disabled_reason
    held = len(op.calls)
    clock[0] += DISABLE_COOLDOWN_SEC - 1.0
    assert d.sync() == 0
    assert len(op.calls) == held, "冷却窗内一次网都不出"
    # token 轮换（或 hub 重启）⇒ 冷却到点后自动复活并把积压推完
    box["status"] = 200
    clock[0] += 2.0
    assert d.sync() == 3
    assert d.disabled_reason == ""
    assert d.status()["disabled_sec"] == 0.0
    assert d.pending() == []
    assert any("冷却结束" in m for m in msgs), msgs


def test_rejected_round_is_skipped_but_the_rest_keep_flowing(tmp_path: Path) -> None:
    """一轮被内容拒收（400）⇒ **只丢这一轮**，其余照推（2026-09-22 事故：旧口径停掉整只腿）。

    现场：it1 的账本行与权重不符（真因是 `_row_for` 取错了同名历史行）→ hub 400 → 旧行为
    「本会话停用补传」⇒ 这一整段后面几十轮再没回过一轮；而那一轮本身根本不该连坐其余轮次。
    """
    root = _make_artifacts(tmp_path / "art")
    msgs: list[str] = []

    class _Op:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
            if url.endswith("/ping"):
                self.calls.append("ping")
                return 200, b"{}"
            it = json.loads(data)["it"]
            self.calls.append(f"post:{it}")
            if it == 1:
                return 400, '{"error":"补传被拒：账本行与权重不符"}'.encode()
            return 200, b"{}"

    op = _Op()
    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="t",
        run_id=RUN,
        artifacts_dir=root,
        opener=op,
        log=msgs.append,
    )
    assert d.sync() == 2, "被拒的那一轮不得连坐后面的轮次"
    assert "post:2" in op.calls and "post:3" in op.calls
    assert d.disabled_reason == "", "内容拒收不是鉴权问题，不得停用整条腿"
    assert d.pending() == [], "被拒轮次不再进待投集（1）、已投的也不进（2/3）"
    assert any("本轮跳过" in m and "其余轮次照推" in m for m in msgs), msgs
    n = len(op.calls)
    d.sync()
    assert len(op.calls) == n, "被拒的那一轮本会话不再重试（新会话会再试一次）"


def test_conflict_409_is_a_content_rejection_not_a_delivery(tmp_path: Path) -> None:
    """★ 2026-10-11 事故的**客户端**半边（评审 F1）：409 = 内容拒收，不是「已投递」。

    旧代码把 409 与 200/201 放同一个分支（`_delivered.add(it)` + `return "ok"`）⇒ hub 说
    「同一轮已有**不同**字节」时，这一轮被记成投递成功、本会话永不重试，日志还写「幂等」——
    与 hub 侧新闸的「响亮、不静默丢」正好相反（只改 hub = 冲突被静默吞掉）。
    改后：这一轮进 `rejected`（**带权重指纹**，跨会话生效）、其余轮次照推、整条腿不停用。
    """
    root = _make_artifacts(tmp_path / "art")
    msgs: list[str] = []

    class _Op:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
            if url.endswith("/ping"):
                self.calls.append("ping")
                return 200, b"{}"
            it = json.loads(data)["it"]
            self.calls.append(f"post:{it}")
            if it == 1:
                body = {
                    "status": "conflict",
                    "run_id": RUN,
                    "it": 1,
                    "have_fp": "a" * 64,
                    "got_fp": "b" * 64,
                    "error": "补传冲突 it1：hub 已有**不同**字节的权重——不覆盖、需人裁决",
                }
                return 409, json.dumps(body).encode("utf-8")
            return 200, b"{}"

    op = _Op()
    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="t",
        run_id=RUN,
        artifacts_dir=root,
        opener=op,
        log=msgs.append,
    )
    assert d.sync() == 2, "被 409 拒的那一轮不得连坐后面的轮次"
    assert d.disabled_reason == "", "409 是内容问题，不得停用整条腿"
    assert d.pending() == [], "冲突轮不再进待投集（1）、已投的也不进（2/3）"
    led = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))
    assert led["artifacts"] == [2, 3], "409 那一轮**不算已投递**（旧代码把它记成成功）"
    assert [r["it"] for r in led["rejected"]] == [1], "冲突要落盘（跨会话不再重试）"
    import hashlib

    want_fp = hashlib.sha256((root / "it-001" / "weights.json").read_bytes()).hexdigest()
    assert led["rejected"][0]["weights_fp"] == want_fp, "拒收记账按权重指纹（同一 it 换内容才再试）"
    # 判据用**结构**不用措辞：拒收必须为人留下可裁决的两样东西（原因 + 指纹）
    assert led["rejected"][0]["why"], "拒收要写明原因（人要看的是现场，不是一行「失败」）"
    assert any(m.startswith("补传 it1") for m in msgs), msgs
    n = len(op.calls)
    d.sync()
    assert len(op.calls) == n, "本会话不再重试冲突轮（新会话指纹对得上也不再试）"


def test_row_is_picked_by_matching_bytes_not_by_first_same_iter(tmp_path: Path) -> None:
    """同名 it 有多行（跨会话追加）⇒ 取**与本轮字节相符**的最后一行，不是第一条。

    这正是 2026-09-22 那一次 400 的真因：续跑重跑同一 it，`metrics.jsonl` 追加了新行、
    权重文件被覆写，而旧的「取第一条同 it 行」拿到上一会话的行 ⇒ 行/权重是两个会话的东西。
    """
    import hashlib

    root = _make_artifacts(tmp_path / "art", iters=(1,))
    w2 = json.dumps({"it": 1, "w": 99.0}).encode("utf-8")
    (root / "it-001" / "weights.json").write_bytes(w2)  # 续跑重跑 ⇒ 权重换了字节
    with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"it": 1, "weights_fp": hashlib.sha256(w2).hexdigest(), "wall_sec": 2.0}) + "\n")

    bodies: list[dict] = []

    def op(url: str, data: bytes, headers: dict, timeout: float):
        if url.endswith("/ping"):
            return 200, b"{}"
        bodies.append(json.loads(data))
        return 200, b"{}"

    d = _deliverer("http://hub.invalid", root, opener=op)
    assert d.sync() == 1
    row = bodies[0]["row"]
    assert row is not None and row["wall_sec"] == 2.0, "取的必须是相符的那一行（最后一条）"
    assert bodies[0]["weights_fp"] == hashlib.sha256(w2).hexdigest()


def test_row_with_mismatching_fingerprint_is_dropped_not_sent(tmp_path: Path) -> None:
    """账本里没有一行与本轮字节相符 ⇒ **只发权重、不发 row**（指错轮次的行比缺行危险）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    (root / "it-001" / "weights.json").write_bytes(b'{"it": 1, "w": 123.0}')  # 旧行指纹不符
    bodies: list[dict] = []
    msgs: list[str] = []

    def op(url: str, data: bytes, headers: dict, timeout: float):
        if url.endswith("/ping"):
            return 200, b"{}"
        bodies.append(json.loads(data))
        return 200, b"{}"

    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="t",
        run_id=RUN,
        artifacts_dir=root,
        opener=op,
        log=msgs.append,
    )
    assert d.sync() == 1, "行配不上不该拦住这一轮的权重"
    assert bodies[0]["row"] is None, "配不上的行一律不发（否则 hub 会拒收整个体）"
    assert any("账本行与权重不符" in m for m in msgs), msgs
    assert d.pending() == [] and d.disabled_reason == ""


class _FlakyOpener:
    """前 `ok` 轮成功，之后直接抛（模拟传到一半连接断）。"""

    def __init__(self, ok: int) -> None:
        self.ok = ok
        self.n = 0

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
        if url.endswith("/ping"):
            return 200, b"{}"
        self.n += 1
        if self.n > self.ok:
            raise ConnectionResetError("connection reset by peer")
        return 200, b'{"status":"accepted"}'


def test_transport_exception_mid_batch_keeps_the_ledger_consistent(tmp_path: Path) -> None:
    """传到一半连接断（opener 直接抛）：不冒泡、本轮已投的照旧落账，下轮从断点接上。

    为什么盯这个：异常若冒到 `sync()` 外层，`_save_ledger()` 会被跳过 —— **内存里已投递的
    那几轮没落盘**，下次会话整批重传（幂等，但白烧流量）。修法是把传输异常收在 `_post` 里。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    d = _deliverer("http://hub.invalid", root, opener=_FlakyOpener(ok=1))
    assert d.sync() == 1  # 不抛
    assert json.loads((root / "delivered.json").read_text(encoding="utf-8"))["artifacts"] == [1]
    assert d.pending() == [2, 3]


def test_sync_cap_bounds_one_call_and_leftover_waits_for_next_round(tmp_path: Path) -> None:
    """积压再多也不把一次 `sync()` 拖成不可控的长调用（下轮继续）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3, 4, 5))
        d = _deliverer(base, root, sync_cap=2)
        assert d.sync() == 2
        assert d.pending() == [3, 4, 5]
        assert d.sync() == 2
        assert d.sync() == 1
    finally:
        srv.shutdown()
        th.join(timeout=5)


# ────────────────────────── hub 侧：不可信输入 ──────────────────────────


def test_run_id_traversal_is_rejected_and_writes_nothing(tmp_path: Path) -> None:
    """`run_id` 是 hub 上的目录名 ⇒ `../` 必须在入口被拒（否则就是任意文件写）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        for bad in ("../evil", "..", "a/b", "C:evil", ".hidden", "x" * 65, ""):
            status, body = _http(
                base,
                "sekret",
                OFFLINE_ARTIFACT_PATH,
                "POST",
                data=json.dumps(_artifact_body(root, 1, run_id=bad)).encode("utf-8"),
            )
            assert int(status) == 400, (bad, status, body)
        assert not (tmp_path / "evil").exists()
        assert not (tmp_path / "jobs" / "offline").exists()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_fingerprint_mismatch_is_rejected(tmp_path: Path) -> None:
    """传输损坏（声明指纹 ≠ 实收字节）必须**在入口**拦住，而不是让损坏权重进 eval/续跑。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        body = _artifact_body(root, 1, fp="f" * 64)
        status, resp = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(body).encode("utf-8")
        )
        assert int(status) == 400
        assert "指纹" in resp["error"]
        assert not (store.offline_run_dir(RUN) / "it-001" / "weights.json").exists()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_row_fingerprint_mismatch_is_rejected(tmp_path: Path) -> None:
    """账本行自称的指纹 ≠ 实收权重 = **产物目录内部不一致**（人改过/半截写入）。

    这是节点自己产的证据（`ArtifactStore.checkpoint` 写完权重当场算的 sha）；两边对不上
    却收下，等于把一份自相矛盾的产物挂上 hub 的名义。
    """
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        body = _artifact_body(root, 1)
        body["row"] = {"it": 1, "weights_fp": "e" * 64}
        status, resp = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(body).encode("utf-8")
        )
        assert int(status) == 400 and "账本行" in resp["error"]
        assert not (store.offline_run_dir(RUN) / "it-001").exists()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_duplicate_artifact_keeps_the_first_write(tmp_path: Path) -> None:
    """同一轮权重是不可变快照：**同一份字节**重传（重连/重启/重试）⇒ 200 duplicate，不改写。

    幂等重传必须是 200：409 会让节点把它当成失败、每轮把已投过的再传一遍。
    """
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        first = _artifact_body(root, 1)
        it_dir = store.offline_run_dir(RUN) / "it-001"
        assert _http(base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(first).encode())[0] == 200
        w1 = (it_dir / "weights.json").read_bytes()

        status, resp = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(first).encode()
        )
        assert int(status) == 200 and resp["status"] == "duplicate"
        assert (it_dir / "weights.json").read_bytes() == w1
        rows = (store.offline_run_dir(RUN) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        assert len([r for r in rows if r.strip()]) == 1  # 账本一行 = 一轮
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_conflicting_artifact_is_409_and_never_overwrites(tmp_path: Path) -> None:
    """★ 2026-10-11 事故可见面：同一轮已有**不同字节** ⇒ 409 `conflict`（不是 200 duplicate）。

    事故形状：起点（无账本行）先占了 `it-1`，真 it1 推上来被判 duplicate（= 成功！）静默丢弃
    ⇒ 真权重与读数永不到岸，而控制面以为那一轮已经回传了。
    「首写锁定」本身是对的（同一轮是不可变快照），但**不匹配必须响亮**：409 + 两个指纹 +
    不覆盖、不并账、不推进活动权重。
    """
    import hashlib

    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        first = _artifact_body(root, 1)
        it_dir = store.offline_run_dir(RUN) / "it-001"
        assert _http(base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(first).encode())[0] == 200
        w1 = (it_dir / "weights.json").read_bytes()

        w2 = b'{"it":1,"w":999}'
        other = dict(first)
        other["weights_json"] = encode_weights_json(w2)  # 另一份字节
        other["weights_fp"] = hashlib.sha256(w2).hexdigest()
        status, resp = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(other).encode()
        )
        assert int(status) == 409 and resp["status"] == "conflict"
        assert resp["have_fp"] == hashlib.sha256(w1).hexdigest()
        assert resp["got_fp"] == hashlib.sha256(w2).hexdigest()
        assert (it_dir / "weights.json").read_bytes() == w1, "不得覆盖已有产物"
        rows = (store.offline_run_dir(RUN) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        assert len([r for r in rows if r.strip()]) == 1, "冲突不并账（不新增行）"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_rowless_rounds_are_never_delivered(tmp_path: Path) -> None:
    """★ 2026-10-11 事故回归（客户端）：**没有账本行**的 `it-NNN` 不上行。

    起点是 `ArtifactStore.checkpoint(row=None)` 落的 —— 全仓唯一不收 `row` 的调用点
    （`remote/plan_handoff.py::_seed_start_checkpoint`）⇒ 它没有 `metrics.jsonl` 行，而每一轮
    真训练都有。旧 `pending()` 只认「目录名 + weights.json」，两者完全不可区分：事故里起点被
    当成 it1 推上 hub、首写锁定 ⇒ 真 it1（权重 + 读数）永不到岸。
    这里用**两个同形状目录**（一个无行、一个有行）把「行」这件事单独拿出来钉。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    seed = root / "it-002"  # 起点形状：只落权重、**没有账本行**
    seed.mkdir()
    (seed / "weights.json").write_bytes(b'{"it": 2, "start": true}')
    d = _deliverer("http://hub.invalid", root)
    assert d.pending() == [1], "只有有账本行的那一轮才算待投递（起点不上行）"


def test_rowless_round_does_not_advance_the_active_weights(tmp_path: Path) -> None:
    """★ 2026-10-11 事故的第二道防线（hub 侧）：无账本行 ⇒ **不推进活动权重**。

    事故里假 it1（无账本行、无 opt）在 `advance_active` 为真时把 `<traj>/weights.json` 推到了
    起点 —— hub 的「当前进度」白涨一格，而那一轮的真实读数并不存在。
    镜像/归档**照落**（它们是「算过什么」的证据），只有活动权重这一格被挡；
    判据看**请求体**的 `row`（落盘后的 `row.json` 无论真假都存在，反推不出来）。
    """
    base, store, srv, th = _boot_server(tmp_path)
    try:
        traj = store.jsonl_path.parent
        root = _make_artifacts(tmp_path / "art")
        rowless = _artifact_body(root, 1)
        rowless.pop("row")  # 起点形状：没有账本行
        st, res = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(rowless).encode()
        )
        assert int(st) == 200 and res["status"] == "accepted", res  # 权重照收
        assert (store.offline_run_dir(RUN) / "it-001" / "weights.json").is_file(), "镜像照落"
        assert not (traj / "weights.json").exists(), "无账本行不得推进活动权重"
        # 反向（防「这道断言恒真」）：带账本行的真轮次必须推进
        real = _artifact_body(root, 2)
        st, res = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(real).encode()
        )
        assert int(st) == 200 and res["status"] == "accepted", res
        assert (traj / "weights.json").read_bytes() == (
            root / "it-002" / "weights.json"
        ).read_bytes(), "真轮次才推进活动权重"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_offline_result_overwrites_latest_and_is_audited(tmp_path: Path) -> None:
    """段末摘要是「这条腿现在到哪了」的最新答案 ⇒ 覆盖写（与不可变的逐轮产物相反）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        for it_end, state in ((5, "running"), (9, "complete")):
            status, _resp = _http(
                base,
                "sekret",
                OFFLINE_RESULT_PATH,
                "POST",
                data=json.dumps({"run_id": RUN, "it_end": it_end, "state": state}).encode("utf-8"),
            )
            assert int(status) == 200
        rec = json.loads((store.offline_run_dir(RUN) / "result.json").read_text(encoding="utf-8"))
        assert rec["it_end"] == 9 and rec["state"] == "complete"
        ledger = store.jsonl_path.read_text(encoding="utf-8")
        assert ledger.count('"offline_result"') == 2  # 审计留痕（每次上浮一条）
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_offline_endpoints_require_auth(tmp_path: Path) -> None:
    """补传端点与其他端点同一条鉴权边界（Bearer），不是匿名接收器。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        body = json.dumps(_artifact_body(root, 1)).encode("utf-8")
        status, _ = _http(base, "wrong-token", OFFLINE_ARTIFACT_PATH, "POST", data=body)
        assert int(status) == 401
        status, _ = _http(
            base,
            "wrong-token",
            OFFLINE_RESULT_PATH,
            "POST",
            data=json.dumps({"run_id": RUN, "it_end": 1, "state": "x"}).encode(),
        )
        assert int(status) == 401
        assert not (tmp_path / "jobs" / "offline").exists()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_oversized_body_is_rejected(tmp_path: Path) -> None:
    """声明超过上限的体直接 413——不读进内存、不落盘。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        status, resp = _http(
            base,
            "sekret",
            OFFLINE_ARTIFACT_PATH,
            "POST",
            data=json.dumps({"run_id": RUN, "it": 1, "weights_json": "x" * 100}).encode("utf-8"),
            extra_headers={"Content-Length": str(64 * 1024 * 1024)},
        )
        assert int(status) == 413, resp
    finally:
        srv.shutdown()
        th.join(timeout=5)


# ────────────────────────── sanitize_run_id（纯函数） ──────────────────────────


def test_sanitize_run_id_accepts_real_ids_and_rejects_path_shapes() -> None:
    assert sanitize_run_id("x3-rebirth-a2-20260917-a1b2c3") == "x3-rebirth-a2-20260917-a1b2c3"
    assert sanitize_run_id(" run_1.0 ") == "run_1.0"
    for bad in ("", "  ", "..", "../x", "a/b", "a\\b", "C:x", ".hidden", "-x", "x" * 65, None, 7):
        with pytest.raises(ProtocolError):
            sanitize_run_id(bad)


# ────────────────────────── 与训练集成（真端点，替身 run_job） ──────────────────────────


def test_run_plan_job_delivers_each_round_while_the_segment_runs(tmp_path: Path) -> None:
    """半离线段的每轮都在**跑的过程中**到达 hub（不是等段尾一次性交）；段末摘要落 state。"""
    plan, m, job_dir, first = _prepare(tmp_path, iters=4, start_it=1)
    base, store, srv, th = _boot_server(tmp_path)
    try:
        result = run_plan_job(
            job_id=m["job_id"],
            manifest=m,
            job_dir=job_dir,
            work_dir=tmp_path / "work",
            plan=plan,
            plan_sha256=m["plan_sha256"],
            first_result=first,
            artifacts_dir=tmp_path / "art",
            hub_url=base,
            hub_token="sekret",
            run_job_fn=_FakeRunJob(tmp_path),
            log=_quiet,
        )
        run_dir = store.offline_run_dir("run-runloop")
        delivered = sorted(
            int(p.name[3:]) for p in run_dir.glob("it-*") if (p / "weights.json").exists()
        )
        # 锚点轮（= manifest.it = 2）+ 逐轮都到了；★ 而**起点（plan.start_it = 1）不到 hub**
        # ---- 2026-10-11 事故的正面回归：那一份曾经被当成 it1 推上来、锁死 hub 首写。
        assert delivered == [2, 3, 4]
        assert not (run_dir / "it-001").exists(), "起点永不回传（D1）"
        rec = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        assert rec["it_end"] == result["it_end"] == 4 and rec["state"] == "complete"
        # T6：跑到计划终点 ⇒ 段末摘要自报 `end_it_reached`（控制台导入后据此转交 run_complete）
        assert rec["end_it_reached"] is True
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_max_iters_capped_segment_does_not_claim_end_it_reached(tmp_path: Path) -> None:
    """T6 负向：`max_iters` 截断的段跑完也**不算**跑满（计划终点没到）。

    这是最容易做错的一格：`stopped=="complete"` 只说明「这拍没活了」，而老实报
    `end_it_reached` 的是「计划终点已到」——两者在截断/预算场景下并不等价。
    """
    plan, m, job_dir, first = _prepare(tmp_path, iters=4, start_it=1)
    base, store, srv, th = _boot_server(tmp_path)
    try:
        result = run_plan_job(
            job_id=m["job_id"],
            manifest=m,
            job_dir=job_dir,
            work_dir=tmp_path / "work",
            plan=plan,
            plan_sha256=m["plan_sha256"],
            first_result=first,
            artifacts_dir=tmp_path / "art",
            max_iters=2,
            hub_url=base,
            hub_token="sekret",
            run_job_fn=_FakeRunJob(tmp_path),
            log=_quiet,
        )
        assert result["it_end"] == 3 and result["run_state"] == "complete"
        rec = json.loads(
            (store.offline_run_dir("run-runloop") / "result.json").read_text(encoding="utf-8")
        )
        assert rec["it_end"] == 3 and rec["end_it_reached"] is False
    finally:
        srv.shutdown()
        th.join(timeout=5)


# ─────────────────── 归位键（多课程 hub：补传必须落进本课） ───────────────────


def test_deliverer_omits_course_key_when_unset(tmp_path: Path) -> None:
    """没给课程 ⇒ 体里**不带** course 键（单课程 hub 的键就是空串，逐字回到旧形状）。

    与「带一个空串」的差别是实打实的：hub 的 `locate_offline_course` 把「体里带没带」
    当作第一个判据，多一个空字符串键就等于要在那里多一层归一化。"""
    seen: list[dict] = []

    def opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        if url.endswith("/offline/artifact"):
            seen.append(json.loads(data.decode("utf-8")))
        return 200, b"{}"

    root = _make_artifacts(tmp_path / "art")
    d = _deliverer("http://hub", root, opener=opener)
    assert d.sync() == 3
    assert all("course" not in b for b in seen) and len(seen) == 3
    assert d.status()["course"] == ""


def test_deliverer_carries_course_so_a_multi_course_hub_can_route(tmp_path: Path) -> None:
    """给了课程 ⇒ 逐轮体与段末摘要**都**带 `course`。

    真 hub 下的后果（test_multi_course_hub 有端到端）：不带它就 400「无法归属课程」
    ⇒ 补传整个停掉（体是自己造的，重试不会变对）⇒ 控制台上只剩「跑完自己下载导入」。
    所以这个键是**多课程 hub 下补传能不能用**的关键，不是可选装饰。"""
    seen: list[dict] = []

    def opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        if data:  # `/ping` 的体是空的（非 JSON）——只收有体的那几个端点
            seen.append(json.loads(data.decode("utf-8")))
        return 200, b"{}"

    root = _make_artifacts(tmp_path / "art")
    d = _deliverer("http://hub", root, course="c5-gae", opener=opener)
    assert d.sync() == 3
    assert d.deliver_result(it_end=3, state="complete")
    assert seen and all(b.get("course") == "c5-gae" for b in seen)
    assert d.status()["course"] == "c5-gae"


def test_make_deliverer_passes_the_course_through() -> None:
    """工厂也接这个参数（错过它 = 参数在构造链上静默丢掉，与「没实现」同效）。"""
    from remote.deliver_proc import make_deliverer

    d = make_deliverer(
        hub_url="http://hub", hub_token="t", run_id=RUN, artifacts_dir="x", course="c4"
    )
    assert d is not None and d.course == "c4"


# ────────────── 归属身份：可读名（plan/offline-leg-ppo-attribution，2026-10-12） ──────────────
#
# dashboard 的 PPO 组按补传体里的身份列行；可读名（`kaggle-o`）**只有客户端自报**这一条
# 来源（hub 侧只有 claim 身份 `{HOSTNAME}-{uuid8}`，变不出 env/link）。

def test_deliverer_carries_the_readable_worker_name_when_it_has_one(tmp_path: Path) -> None:
    """可读名随**每一轮**的体上报；没给时**连键都不带**（不是空串）。

    与 `course` / `lease_token` 同一条纪律：读方把「带没带」当判据 —— 空串会把「没身份」
    写成「有身份 = 空」，而 dashboard 那边就只能把两种情形混成一行。
    """
    seen: list[dict] = []

    def opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        if url.endswith("/offline/artifact"):
            seen.append(json.loads(data.decode("utf-8")))
        return 200, b"{}"

    root = _make_artifacts(tmp_path / "art")
    named = _deliverer("http://hub", root, opener=opener, worker_name="kaggle-o")
    assert named.sync() == 3
    assert len(seen) == 3 and all(b.get("worker_name") == "kaggle-o" for b in seen)
    seen.clear()
    plain = _deliverer("http://hub", _make_artifacts(tmp_path / "art2"), opener=opener)
    assert plain.sync() == 3
    assert len(seen) == 3 and all(b.keys().isdisjoint({"worker_name"}) for b in seen)


def test_make_deliverer_passes_the_worker_name_through() -> None:
    """工厂两种模式都要透传（错过工厂 = 参数在构造链上静默丢掉，与没实现同效）。

    `process` 那一跳（父→子的 env）另有两层证：拉起面的 env 在
    `test_offline_deliver_async.py`，真子进程的体在 `test_offline_deliver_proc.py`。
    """
    from remote.deliver_proc import make_deliverer

    for mode in ("thread", "sync"):
        d = make_deliverer(
            hub_url="http://hub",
            hub_token="t",
            run_id=RUN,
            artifacts_dir="x",
            worker_name="kaggle-o",
            mode=mode,
        )
        assert d is not None and d.worker_name == "kaggle-o", mode


def test_deliverer_carries_the_lease_token_only_when_it_has_one(tmp_path: Path) -> None:
    """★M1b / P1-1：体里带 `lease_token` ⇒ hub 才让这一轮**推进活动起点**。

    没有租约时**连键都不带**（不是带空串）：hub 侧 `offline_advance_ok` 把「空 token」
    当成「不是持有人」⇒ 带空串等于把「我没租约」写成「我持了一个空租约」。
    """
    seen: list[dict] = []

    def opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        if url.endswith("/offline/artifact"):
            seen.append(json.loads(data.decode("utf-8")))
        return 200, b"{}"

    root = _make_artifacts(tmp_path / "art")
    with_lease = _deliverer("http://hub", root, opener=opener, hub_lease="lease-1")
    assert with_lease.sync() == 3
    assert len(seen) == 3 and all(b.get("lease_token") == "lease-1" for b in seen)
    assert with_lease.status()["lease"] is True
    # 另一份产物目录（无租约；`delivered.json` 是每目录一份）——键整个消失
    seen.clear()
    plain = _deliverer("http://hub", _make_artifacts(tmp_path / "art2"), opener=opener)
    assert plain.sync() == 3
    assert len(seen) == 3 and all("lease_token" not in b for b in seen)
    assert plain.status()["lease"] is False





def test_run_plan_job_ignores_delivery_failures(tmp_path: Path, hub_down: _RefusingHub) -> None:
    """补传坏掉（hub 没人听）时整段照常跑完 —— 这是本功能唯一不可让步的性质。"""
    plan, m, job_dir, first = _prepare(tmp_path, iters=3, start_it=1)
    result = run_plan_job(
        job_id=m["job_id"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        artifacts_dir=tmp_path / "art",
        hub_url="http://127.0.0.1:1",
        hub_token="sekret",
        run_job_fn=_FakeRunJob(tmp_path),
        log=_quiet,
    )
    assert result["it_end"] == 3 and result["run_state"] == "complete"
    assert hub_down.calls, "整段跑完了却一次补传都没试过 —— 「补传坏掉」不是被测出来的"
    assert (tmp_path / "art" / "artifacts.zip").exists()  # 产物照常
    assert not (tmp_path / "art" / "delivered.json").exists()  # 一次都没投成


# ══════ 抽屉的两个账本 + 容量读数（D8，plan §3.2/§3.3）══════


def test_drawer_carries_the_two_ledgers_and_recopies_only_what_changed(tmp_path: Path) -> None:
    """DoD-2a：抽屉里有 `metrics.jsonl`/`eval_log.jsonl`，且**变了才重拷**（D8）。

    现场（报告 §3.1）：抽屉里只有 `weights`/`opt` ⇒ 导入后曲线与 eval 全空，得靠人从 `/tmp` 抢救。
    后一半是容量纪律（§14）：两个账本是追加型，每拍全量重写会把网络盘写放大到 MB/轮。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _write_eval_log(root, it=1, games=2)
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 1
    led, ev = drawer / "metrics.jsonl", drawer / "eval_log.jsonl"
    assert led.read_bytes() == (root / "metrics.jsonl").read_bytes(), "逐轮读数得能随抽屉回来"
    assert ev.read_bytes() == (root / "eval_log.jsonl").read_bytes(), "eval 行同理（控制台与门判只看课程账本）"
    assert (drawer / "manifest.json").is_file(), "锚点照旧齐（抽屉自己就是一份能被导入吃的产物根）"
    sig = (led.stat().st_mtime_ns, ev.stat().st_mtime_ns)
    # 第 2 拍：it1 被重跑过（`made>0`）而**两个账本都没变** ⇒ 一个都不重写
    _rewrite_round(root, 1, w=42.0)
    assert d.backup_pending() == 1
    assert (led.stat().st_mtime_ns, ev.stat().st_mtime_ns) == sig
    assert d.status()["backup_skipped"] == 2
    # 第 3 拍：新轮次 ⇒ metrics 追加了一行（**只有它**变）⇒ 只重拷它
    _add_round(root, 2)
    assert d.backup_pending() == 1
    assert led.stat().st_mtime_ns != sig[0] and ev.stat().st_mtime_ns == sig[1]
    assert d.status()["backup_skipped"] == 1


def test_status_reports_the_drawer_capacity_line(tmp_path: Path) -> None:
    """DoD-2c：抽屉字节预算行进 `status()`（§14 的容量行）。

    读方**零 IO**（每拍被调，而抽屉在网络盘上）：读数由写方在 `backup_pending()` 里顺手量。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _write_eval_log(root, it=1, games=1)
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.status()["drawer_bytes"] == 0, "还没写过 ⇒ 没有读数（也不该为了读数去走目录）"
    assert d.backup_pending() == 1
    st = d.status()
    want_led = (drawer / "metrics.jsonl").stat().st_size + (drawer / "eval_log.jsonl").stat().st_size
    assert st["drawer_bytes"] > 0 and st["drawer_ledger_bytes"] == want_led
    assert st["drawer_round_avg_bytes"] > 0
    assert st["drawer_budget_bytes"] == st["pending"] * st["drawer_round_avg_bytes"] + st["drawer_ledger_bytes"]


def test_child_status_surface_carries_the_drawer_readings(tmp_path: Path) -> None:
    """子进程状态面是**白名单**（`deliver_worker._write_status`）⇒ 新读数不写进去 = 没实现（控制台看不见）。"""
    from remote.deliver_worker import CtlReader, _write_status  # type: ignore

    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _write_eval_log(root, it=1, games=1)
    d = _deliverer("", root, backup_root=tmp_path / "net" / "backup")
    assert d.backup_pending() == 1
    sp = tmp_path / "status.json"
    _write_status(sp, d, ctl=CtlReader(tmp_path / "ctl.jsonl", 0), last_ctl_at=0.0)
    got = json.loads(sp.read_text(encoding="utf-8"))
    for k in (
        "drawer_bytes",
        "drawer_ledger_bytes",
        "drawer_round_avg_bytes",
        "drawer_budget_bytes",
        "backup_skipped",
    ):
        assert k in got, k


# ══════ `rejected` 的 TTL / 上限 / 人工重置（D6，plan §15）══════


def _rejected_row(root: Path, it: int, *, at: float, tries: int = 1, why: str = "400") -> dict:
    return {"it": it, "weights_fp": _weights_fp(root, it), "why": why, "at": at, "tries": tries}


def test_rejected_round_comes_back_after_the_ttl(tmp_path: Path) -> None:
    """D6：拒收不是永久判决 —— 过 TTL 的那条记录不再被认领，那一轮重新回到待投集。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(
        root,
        artifacts=[1],
        rejected=[_rejected_row(root, 2, at=time.time() - REJECTED_TTL_SEC - 60)],
    )
    d = _deliverer("", root)
    assert 2 in d.pending(), "权重还在本地盘上，过期的拒收不该再拦它"
    assert d.status()["rejected"] == {}
    assert d.status()["rejected_ttl_sec"] == REJECTED_TTL_SEC


def test_rejected_round_within_the_ttl_is_still_skipped(tmp_path: Path) -> None:
    """反方向（旧行为保留）：TTL 内的指纹相符记录照旧生效 —— 否则「同一轮反复重投 ~1.9MB」。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(root, artifacts=[1], rejected=[_rejected_row(root, 2, at=time.time() - 60)])
    d = _deliverer("", root)
    assert d.pending() == []
    assert d.status()["rejected"] == {2: "400"}
    assert d.status()["rejected_age"][2][0] == 1


def test_rejected_round_at_the_try_limit_comes_back(tmp_path: Path) -> None:
    """同一个 it 被反复判成内容问题（不是「换了一轮内容」）⇒ 到上限就不该再无限期挨着。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(
        root,
        artifacts=[1],
        rejected=[_rejected_row(root, 2, at=time.time(), tries=REJECTED_MAX_TRIES)],
    )
    assert 2 in _deliverer("", root).pending()


def test_a_rejected_row_without_age_comes_back(tmp_path: Path) -> None:
    """旧账本（这门功能之前写的）没有 `at`：一条没有年龄的证据不足以支撑「永不再试」⇒ 一律复活。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(
        root,
        artifacts=[1],
        rejected=[{"it": 2, "weights_fp": _weights_fp(root, 2), "why": "旧账本"}],
    )
    assert 2 in _deliverer("", root).pending()


def test_new_rejection_records_its_age_and_tries(tmp_path: Path) -> None:
    """新记一笔拒收要带上 `at`/`tries`（不带 = 下一次加载就只剩「永久有效」这一种语义）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    d = _deliverer("", root)
    assert d._reject_round(1, "体形状不对", weights_fp="f" * 64) == "skip"
    d._save_ledger()
    row = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["rejected"][0]
    assert row["at"] > 0 and row["tries"] == 1
    d._reject_round(1, "又一次", weights_fp="f" * 64)
    d._save_ledger()
    row = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["rejected"][0]
    assert row["tries"] == 2, "同一轮再判一次要计数（上限靠它）"


def test_a_rejected_only_tap_still_persists_the_record(tmp_path: Path) -> None:
    """D6：一拍里**只有**拒收时记录也得落盘 —— 否则下一次落盘会把它静默抹掉（连 TTL 都轮不到）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))

    def op(url: str, data: bytes, headers: dict, timeout: float):
        if url.endswith("/ping"):
            return 200, b"{}"
        return 400, b'{"error":"bad body"}'

    d = _deliverer("http://hub.invalid", root, opener=op)
    assert d.sync() == 0
    rows = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["rejected"]
    assert [r["it"] for r in rows] == [1, 2]
    assert all(r["at"] > 0 and r["tries"] == 1 for r in rows)


def test_reset_rejected_revives_the_round_even_while_the_session_is_alive(tmp_path: Path) -> None:
    """D6 的人工出口：`reset_rejected()` 清掉记录，**活着的会话下一次落盘不许把它写回来**。

    只清文件是不够的：补传子进程的内存里还留着那份拒收（`_rejected`），下一次 `_save_ledger()`
    会把它原样写回 —— 运维看到的「清掉了」会在几分钟后自己复原。所以水位也一并记进文件。
    """
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(root, artifacts=[1], rejected=[_rejected_row(root, 2, at=time.time())])
    d = _deliverer("", root)
    assert 2 in d._rejected and 2 not in d.pending()
    assert reset_rejected(root, run_id=RUN, it=2, log=_quiet) == 1
    d._save_ledger()  # 活着的会话落盘一次
    assert 2 not in d._rejected and 2 in d.pending(), "重置必须真的生效，而不是被下一次落盘覆盖回去"
    data = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))
    assert data["rejected"] == [] and REJECTED_RESET_KEY in data
    assert json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["artifacts"] == [1]


def test_reset_rejected_only_touches_the_named_round(tmp_path: Path) -> None:
    """只重置一环不得把其余环次的拒收一并放了（水位按 `it` 记，不是一条全局水位）。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    now = time.time()
    _write_ledger(
        root,
        rejected=[_rejected_row(root, 2, at=now), _rejected_row(root, 3, at=now, why="409")],
    )
    d = _deliverer("", root)
    assert sorted(d._rejected) == [2, 3]
    assert reset_rejected(root, run_id=RUN, it=2, log=_quiet) == 1
    d._save_ledger()
    assert sorted(d._rejected) == [3], "it3 的拒收不得被连坐放掉"
    assert 2 in d.pending() and 3 not in d.pending()


def test_reset_rejected_refuses_another_runs_ledger(tmp_path: Path) -> None:
    """目录复用（同一个产物根换过 run_id）时，不许拿别的 run 的记录开刀（One-Author 的最小版）。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(root, run_id="other-run", rejected=[_rejected_row(root, 2, at=time.time())])
    assert reset_rejected(root, run_id=RUN, it=2, log=_quiet) == 0
    data = json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))
    assert [r["it"] for r in data["rejected"]] == [2]


# ══════ 抽屉的判据化删除 + 重试可观测面（D3，plan §4.2-1/3）══════


def _drawer_rounds(drawer: Path) -> list[str]:
    return sorted(d.name for d in drawer.glob("it-*") if d.is_dir())


def test_drawer_prunes_confirmed_rounds_after_they_land(tmp_path: Path) -> None:
    """DoD-3c：`POST 200 → _save_ledger() → 才允许删` —— 回传成功的轮次从抽屉里消失。

    顺序就是这个用例的全部：`_delivered` 只在 200/201 时追加（且先落账本），而清理只读它。
    """
    base, _store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art", iters=(1, 2))
        drawer = tmp_path / "net" / "backup"
        d = _deliverer(base, root, backup_root=drawer)
        assert d.backup_pending() == 2, "先推不出去的那一拍：两轮进抽屉"
        assert _drawer_rounds(drawer) == ["it-001", "it-002"]
        assert d.sync() == 2, "hub 通了就都推上去"
        assert d.status()["backup_pruned"] == 2, "已确认送达 ⇒ 抽屉里那两轮该删掉"
        assert _drawer_rounds(drawer) == []
        assert (root / "it-001" / "weights.json").is_file(), "删的只是抽屉那一份（本地产物不动）"
        # 锚点留着（抽屉仍是一份能吃的产物根；它们不按轮次删）。本 fixture 只有这两件
        # （`_make_artifacts` 不写 `state.json`）——所以断言只盯着**源里真有的**那几件。
        assert (drawer / "manifest.json").is_file() and (drawer / "metrics.jsonl").is_file()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_drawer_never_prunes_when_the_ledger_is_unreadable(tmp_path: Path) -> None:
    """★ DoD-3c 反探针：`delivered.json` 坏了 ⇒ **一个字节都不删**。

    抽屉是**跨会话的唯一第二副本**（`/tmp` 一关就只剩它）⇒ 删它必须同时有「hub 收过」与「账本
    记得」两个证据；读不出来就是没证据（`_load_ledger` 返回空 ⇒ `_ledger_readable` 仍为假）。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 2
    # 账本被破坏（半截写入 / 人手动改坏）——同时把这一份标记成「已投递」（内存里）
    (root / OFFLINE_DELIVERED_NAME).write_text("{坏的 json", encoding="utf-8")
    d2 = _deliverer("", root, backup_root=drawer)
    assert d2._ledger_readable is False
    d2._delivered.add(1)
    d2._prune_forced = True
    assert d2.backup_pending() == 0
    assert _drawer_rounds(drawer) == ["it-001", "it-002"], "账本不可读 ⇒ 一个都不许删"
    assert "账本不可读" in str(d2.status()["prune_skipped"])


def test_drawer_keeps_orphans_and_counts_them(tmp_path: Path) -> None:
    """★ DoD-3e：换过 run_id / 别的机器留下的轮次（账本无证据且本地无同份）⇒ **保留 + 计数**。

    账本是 **run 级**、抽屉是 **课级** ⇒ 续段换 run_id 后老段遗留的轮次在新账本里没有任何证据。
    删它就是把「唯一第二副本」永久丢掉；不删就必须可见（`backup_orphans`）——不许默默不删。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 1
    # 老段遗留：抽屉里多出两轮（本地已没有那份）
    for it in (7, 8):
        (drawer / f"it-{it:03d}").mkdir()
        (drawer / f"it-{it:03d}" / "weights.json").write_bytes(b"old")
    (root / OFFLINE_DELIVERED_NAME).write_text(
        json.dumps({"run_id": RUN, "artifacts": [1]}), encoding="utf-8"
    )
    d2 = _deliverer("", root, backup_root=drawer)
    d2._prune_forced = True
    assert d2.backup_pending() == 0
    assert _drawer_rounds(drawer) == ["it-007", "it-008"], "孤儿保留"
    st = d2.status()
    assert (st["backup_pruned"], st["backup_orphans"]) == (1, 2), "已送达的 it1 删掉 + 两个孤儿进计数"


def test_drawer_prunes_an_orphan_only_when_the_local_copy_matches(tmp_path: Path) -> None:
    """DoD-3e 的另一半：本地有**同一份字节** ⇒ 那是可重建的冗余 ⇒ 可删。

    「本地在」不够（要逐件对账）：本机同一轮被重跑过时本地那份已经不同了，把那时的孤儿删掉
    没什么损失，但把**字节不同**的抽屉份当成「本地有」就会把唯一的旧副本删掉。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 1
    (root / OFFLINE_DELIVERED_NAME).write_text(
        json.dumps({"run_id": RUN, "artifacts": []}), encoding="utf-8"
    )  # 账本**可读**但这一轮没有送达证据
    d2 = _deliverer("", root, backup_root=drawer)
    d2._prune_forced = True
    assert d2.backup_pending() == 0
    assert _drawer_rounds(drawer) == [], "本地逐件与抽屉一致 ⇒ 可删"
    assert d2.status()["backup_pruned"] == 1


def test_drawer_prune_failure_is_only_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """删不掉只 WARN（下拍再试）—— 抽屉是第二副本，删不动绝不能把补传腿拖住或弄抛。"""
    import remote.backup_drawer as bd

    root = _make_artifacts(tmp_path / "art", iters=(1,))
    drawer = tmp_path / "net" / "backup"
    d = _deliverer("", root, backup_root=drawer)
    assert d.backup_pending() == 1
    msgs: list[str] = []
    d.log = msgs.append
    (root / OFFLINE_DELIVERED_NAME).write_text(
        json.dumps({"run_id": RUN, "artifacts": [1]}), encoding="utf-8"
    )
    d2 = _deliverer("", root, backup_root=drawer)
    d2.log = msgs.append

    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("占住了")

    monkeypatch.setattr(bd.shutil, "rmtree", _boom)
    d2._prune_forced = True
    assert d2.backup_pending() == 0  # 不抛
    assert _drawer_rounds(drawer) == ["it-001"]
    assert d2.status()["prune_failed"] == 1
    assert any("抽屉清理 it1 失败" in m for m in msgs), msgs


def test_status_reports_the_retry_face(tmp_path: Path) -> None:
    """plan §4.2-1：`retry_n`/`last_error`/`next_try_sec` 进 `status()`（给人看，不做退避判决）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    clock = [1000.0]
    op = _CountingOpener(0, b"")  # 0 = 连不上
    d = _deliverer("http://hub.invalid", root, opener=op, now_fn=lambda: clock[0])
    assert d.sync() == 0
    st = d.status()
    assert st["retry_n"] == 1 and st["last_error"]
    assert 0.0 < st["next_try_sec"] <= PROBE_FAIL_TTL_SEC, st
    assert d.sync() == 0
    assert d.status()["retry_n"] == 2, "连续失败要累加（人一眼看出「一直在试、一直没成」）"


def test_network_recovers_within_the_short_fail_ttl_without_new_rounds(tmp_path: Path) -> None:
    """★ DoD-3a：拔网 → 恢复（不投入新轮）⇒ 从**最早**待投 it 起补齐。

    反探针在下面：把这个短负缓存改回平坦的 60s（`probe_ttl` 的默认档）⇒ **同一段代码就不该补**，
    于是这条用例真的在量那个常量（而不是「跑一遍就行」）。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    drawer = tmp_path / "net" / "backup"
    clock = [1000.0]
    box = {"up": False}
    posted: list[int] = []

    def op(url: str, data: bytes, headers: dict, timeout: float):
        if not box["up"]:
            raise OSError("网络抖了")
        if url.endswith("/ping"):
            return 200, b"{}"
        posted.append(int(json.loads(data.decode("utf-8"))["it"]))
        return 200, b"{}"

    d = _deliverer("http://hub.invalid", root, opener=op, backup_root=drawer, now_fn=lambda: clock[0])
    assert d.sync() == 0, "拔网 ⇒ 一轮都没推出去"
    assert d.status()["next_try_sec"] <= PROBE_FAIL_TTL_SEC
    clock[0] += PROBE_FAIL_TTL_SEC + 0.1  # 只推进假时钟：**一个新手都不投**
    box["up"] = True
    assert d.sync() == 3, "隧道回来 ⇒ 无需新轮就把积压补齐"
    assert posted == [1, 2, 3], "从最早的待投 it 开始（顺序 = `pending()` 升序）"
    assert d.pending() == [] and d.status()["retry_n"] == 0
    assert _drawer_rounds(drawer) == [], "补齐后抽屉同步清空（同一拍内）"

    # 反探针：把失败负缓存拉长到 60s（= 旧的平坦口径）⇒ 同一个 20s 的窗口里它**不该**再出门。
    root2 = _make_artifacts(tmp_path / "art2", iters=(1, 2, 3))
    clock2 = [1000.0]
    box2 = {"up": False}

    def op2(url: str, data: bytes, headers: dict, timeout: float):
        if not box2["up"]:
            raise OSError("网络抖了")
        if url.endswith("/ping"):
            return 200, b"{}"
        return 200, b"{}"

    # 把**失败**负缓存扳回旧的平坦口径（60s）：同一条代码路径下，20s 的窗口就补不动了
    # ⇒ 这条用例真的挂在 `PROBE_FAIL_TTL_SEC`（那个默认档）上，而不是「跑一遍就行」。
    d2 = _deliverer(
        "http://hub.invalid",
        root2,
        opener=op2,
        probe_ttl=PROBE_TTL_SEC,
        probe_fail_ttl=PROBE_TTL_SEC,
        now_fn=lambda: clock2[0],
    )
    assert d2.sync() == 0
    box2["up"] = True
    clock2[0] += PROBE_FAIL_TTL_SEC + 0.1
    assert d2.sync() == 0, "探活窗未过 ⇒ 一轮也不出门（反探针：判据真的挂在那个常量上）"
    clock2[0] += PROBE_TTL_SEC
    assert d2.sync() == 3, "窗过才补"


def test_a_stale_lease_still_lands_the_artifact(tmp_path: Path) -> None:
    """DoD-3b（补传腿那一半）：租约过期/被抢 ⇒ **照落**，只是不推进活动起点。

    `/offline/artifact` 本来就没有租约门（hub/offline.py）——本用例把它钉住：带一个没人认识
    的 lease 推上去，产物必须到达（现场「租约失效 ⇒ 整段不传」的另一半成因在这里）。
    """
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art", iters=(1,))
        d = _deliverer(base, root, hub_lease="lease-nobody-has")
        assert d.sync() == 1
        run_dir = store.offline_run_dir(RUN)
        assert (run_dir / "it-001" / "weights.json").read_bytes() == (
            root / "it-001" / "weights.json"
        ).read_bytes()
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_reset_rejected_cli_is_usable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m remote.offline_deliver --artifacts <根> --it N`（operator 入口，不经控制台也走得通）。"""
    import time

    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    _write_ledger(root, rejected=[_rejected_row(root, 2, at=time.time())])
    rc = deliver_main(["--artifacts", str(root), "--run-id", RUN, "--it", "2"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["reset"] == 1
    assert json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["rejected"] == []


# ────────────────── 2026-10-21 事故：抖动期可以失败，**不许卡住** ──────────────────
# plan/offline-deliver-stall.plan.md §4-W1-A / W2 / W1-B′。三条症状各自钉一条：
#   G1 一次数据失败把「hub 可达」判死（下一拍连探活都不做）；
#   G2 队头失败 ⇒ `break` ⇒ 后面的轮次一次都没被试过（现场 it69…it81）；
#   G3 失败只走 300s 节流日志 ⇒ 6 次失败只看见 2 行。


class _ScriptedOpener:
    """按「哪一轮 / 体里有没有 eval_rows / 体多大」脚本化的替身 opener（不碰网络）。

    `record` 里每次调用都留 `(url, it | None, 体字节, eval 行数, timeout)` —— 断言直接读它，
    不靠日志文本（文本是第二事实源）。
    """

    def __init__(
        self,
        *,
        fail_its: set[int] | None = None,
        fail_if_eval_rows: bool = False,
        fail_all: bool = False,
        max_body: int | None = None,
    ) -> None:
        self.fail_its = set(fail_its or ())
        self.fail_if_eval_rows = fail_if_eval_rows
        self.fail_all = fail_all
        self.max_body = max_body
        self.record: list[tuple[str, int | None, int, int, float]] = []

    @property
    def artifact_calls(self) -> list[tuple[str, int | None, int, int, float]]:
        return [r for r in self.record if not r[0].endswith("/ping")]

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
        it: int | None = None
        eval_rows = 0
        if data:
            try:
                body = json.loads(data.decode("utf-8"))
            except ValueError:
                body = {}
            if isinstance(body, dict):
                raw_it = body.get("it")
                it = raw_it if isinstance(raw_it, int) else None
                rows = body.get("eval_rows")
                eval_rows = len(rows) if isinstance(rows, list) else 0
        self.record.append((url, it, len(data), eval_rows, timeout))
        if url.endswith("/ping"):
            return 200, b"{}"
        if self.fail_all or (it is not None and it in self.fail_its):
            raise TimeoutError("The read operation timed out")
        if self.max_body is not None and len(data) > self.max_body:
            raise TimeoutError("The read operation timed out")
        if self.fail_if_eval_rows and eval_rows:
            raise TimeoutError("The read operation timed out")
        return 200, b'{"status":"accepted"}'


def test_data_failure_does_not_poison_reachability(tmp_path: Path) -> None:
    """G1（评审 P1-1）：一件数据没过去 ≠ hub 不可达 —— 下一拍**立刻**重探并接着推，不等负缓存。

    为什么盯这个：旧码在 `_post` 里置 `_reachable=False`，而探活失败是 15s 负缓存 ⇒ 链路其实
    早就好了，下一拍却连 16B 的 `/ping` 都不发、直接返回 0（现场的「卡住」有一半是这里）。
    """
    root = _make_artifacts(tmp_path / "art")
    clock = [1000.0]
    op = _ScriptedOpener(fail_its={1})
    d = _deliverer("http://hub.invalid", root, opener=op, now_fn=lambda: clock[0])

    assert d.sync() == 2  # it2/it3 照推（G2），it1 没过去
    assert d.pending() == [1]
    op.fail_its.clear()  # 链路恢复（**时钟不动**：不许靠 15s 负缓存过期来蒙混过关）
    assert d.sync() == 1
    assert d.pending() == []
    assert len([r for r in op.record if r[0].endswith("/ping")]) >= 2


def test_failed_head_does_not_block_the_rounds_behind_it(tmp_path: Path) -> None:
    """G2：队头失败**不再 `break`** —— 积压 14 轮时后面的轮次必须被尝试（现场 it69…it81 从未 POST）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    op = _ScriptedOpener(fail_its={1})
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 2
    assert [r[1] for r in op.artifact_calls] == [1, 2, 3]
    assert d.pending() == [1]  # 失败的照旧留在积压里（不销账）


def test_transport_failures_are_capped_per_pass(tmp_path: Path) -> None:
    """每次失败已付一个超时窗口 ⇒ 一拍最多吃 `MAX_FAILS_PER_PASS` 次（段末 90s 预算不许被吃穿）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3, 4))
    op = _ScriptedOpener(fail_all=True)
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 0
    assert len(op.artifact_calls) == MAX_FAILS_PER_PASS
    # 安全网（评审 P0-2）：`pending()` 的语义**一点没窄** —— 四轮全在，抽屉照吃。
    assert d.pending() == [1, 2, 3, 4]
    st = d.status()
    assert st["fail_n"] == MAX_FAILS_PER_PASS
    assert st["last_fail_kind"] == "timeout"


def test_failure_logs_first_then_every_tenth_and_reports_recovery(tmp_path: Path) -> None:
    """G3：失败走**三触发**（第 1 次 / 每 10 次 / 恢复时），不再被 300s 节流吞掉。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2))
    op = _ScriptedOpener(fail_all=True)
    msgs: list[str] = []
    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="sekret",
        run_id=RUN,
        artifacts_dir=root,
        opener=op,
        log=msgs.append,
    )

    for _ in range(5):  # 每拍 2 次失败（两轮 × 每拍上限 2）⇒ 5 拍攒到 10 次
        assert d.sync() == 0
    fails = [m for m in msgs if m.startswith("补传失败 x")]
    assert [m.split("（")[0] for m in fails] == ["补传失败 x1", "补传失败 x10"], msgs

    op.fail_all = False
    assert d.sync() == 2
    assert any(m.startswith("链路恢复（失败 10 次") for m in msgs), msgs


def test_final_pass_tries_the_last_round_first(tmp_path: Path) -> None:
    """D-1 末轮强制补发：段末（`force_probe`）那一次把**最新那一轮**排最前，其余照旧升序。"""
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    op = _ScriptedOpener()
    d = _deliverer("http://hub.invalid", root, opener=op, sync_cap=1)

    assert d.sync(force_probe=True) == 1
    assert [r[1] for r in op.artifact_calls] == [3]


def test_budget_clips_the_per_attempt_timeout(tmp_path: Path) -> None:
    """W2：段末传剩余预算 ⇒ 单件超时被夹到剩余量（起跑就不会越界，`terminate()` 不触发）。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    op = _ScriptedOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync(force_probe=True, budget_sec=7.0) == 1
    (timeout,) = [r[4] for r in op.artifact_calls]
    assert timeout <= 7.0, f"预算没夹住单件超时：{timeout}"


def test_big_eval_body_degrades_to_weights_first_then_reposts_the_rows(tmp_path: Path) -> None:
    """W1-B′ 降级档（Q4）：带 400 局评估的大体没过去 ⇒ **权重先到岸**，评估读数另欠一次重投。

    为什么这样切而不是「拆四件」：hub 的 `duplicate` 分支本来就会把重投里的 `eval_rows` 并进
    课程账本 ⇒ 零契约改动、零按件记账、`delivered.json` 的「送达」语义不变。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    _write_eval_log(root, it=1, games=2)
    op = _ScriptedOpener(fail_if_eval_rows=True)
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 0  # 带评估读数的那一体没过去
    assert d.pending() == [1]  # 这一轮没销账
    assert d.unsent_reposts() == [1]  # 但评估读数已经记成欠账（且落盘）
    assert d.status()["degraded"] == [1]
    assert json.loads((root / OFFLINE_DELIVERED_NAME).read_text(encoding="utf-8"))["owed_reposts"] == [1]

    assert d.sync() == 1  # 第二拍：只发权重/opt/账本行 ⇒ 到岸
    assert d.pending() == []
    assert d.unsent_reposts() == [1]  # 评估读数还欠着（等链路好）

    op.fail_if_eval_rows = False  # 链路好了一些（大体能过了）
    assert d.sync() == 1  # 第三拍 = 重投，**重新带全量评估读数**
    assert d.unsent_reposts() == []
    full = [r for r in op.artifact_calls if r[3]]
    assert len(full) == 2, op.record  # 第一次（失败）与重投（成功）都带了评估行


# ────────────────────── W3/Q2 瘦身 + W1-C/D 锚点（2026-10-22） ──────────────────────


class _BodyOpener:
    """把每个**产物体**记下来的 opener（断言读体，不读日志文本——文本是第二事实源）。"""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
        if url.endswith("/ping"):
            return 200, b"{}"
        self.bodies.append(json.loads(data.decode("utf-8")))
        return 200, b'{"status":"accepted"}'


def _heavy_eval_log(root: Path, *, it: int, games: int = 1) -> Path:
    """一份**带分析列**的 eval 账本（行仍取自唯一构造点 `worker.eval_rows.eval_row`）。

    为什么要分析列：白名单的整个意义就是「实时体不带它们」。只写瘦行的话，「去掉了一段
    不存在的字段」也会绿——那是一条空断言。
    """
    from worker.eval_rows import eval_row

    rows = [
        eval_row(
            {
                "win": True,
                "cleared": True,
                "score": 100,
                "kills": 4,
                "ticks": 900,
                "dims": {"loot": {"value": 3}},
                "hitsByKind": [1, 2, 3],
                "killsByKind": [3, 0, 0],
                "exposureByKind": [9, 8, 7],
                "moveHist": [10, 2, 3, 4, 5],
                "decisions": [1, 2, 3, 4],
                "stopRuns": [0, 1],
            },
            it=it,
            key16="k" * 16,
            task=(1, 100 + i),
            node="cloud",
        )
        for i in range(games)
    ]
    p = root / "eval_log.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def _metrics_row(root: Path, it: int, **extra: object) -> None:
    """写一行**与权重字节相符**的账本行（`_row_for` 只认这一种；否则本轮不发 row）。"""
    row = {"it": it, "weights_fp": _weights_fp(root, it), **extra}
    (root / "metrics.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_eval_rows_go_through_the_wire_whitelist_and_local_ledger_is_untouched(
    tmp_path: Path,
) -> None:
    """W3（评审 P0-5）：实时体的逐局行**过白名单**，去重键一个不少，本地账本一字未动。

    白名单之外的列不是丢了：它们逐字留在 `eval_log.jsonl` 里，而那份文件由抽屉与离线包
    整份带走。这条用例同时钉住两件事——「瘦」与「瘦的东西还能在 hub 去重」。
    """
    root = _make_artifacts(tmp_path / "art", iters=(2,))
    p = _heavy_eval_log(root, it=2, games=2)
    before = p.read_bytes()
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 1
    (body,) = op.bodies
    rows = body["eval_rows"]
    assert len(rows) == 2
    # 白名单是**契约**，所以用例自己写一份（不 import 实现里的那个常量：那是拿实现验实现）。
    kept = {
        "event",
        "iter",
        "wver",
        "stage",
        "seed",
        "time",
        "node",
        "outcome",
        "win",
        "cleared",
        "ticks",
        "score",
        "quality",
        "kills",
        "playerDamageTaken",
        "playerDeaths",
        "puGotTank",
        "powerUpsCollected",
        "dims",
    }
    assert set(rows[0]) <= kept, f"白名单之外的东西上了实时体：{sorted(set(rows[0]) - kept)}"
    for k in ("event", "iter", "wver", "stage", "seed"):
        assert k in rows[0], f"去重键被瘦掉了：{k}"
    for k in ("hitsByKind", "killsByKind", "exposureByKind", "moveHist", "decisions", "stopRuns"):
        assert k not in rows[0], f"分析列不该上实时体：{k}"
    assert rows[0]["dims"] == {"loot": 3}, "控制台 spawn 反推要它（`eval_row` 把 dims 拍平成标量）"
    assert p.read_bytes() == before, "本地账本是交付面，瘦身只该发生在**上行**那一份上"
    raw, wire = d.status()["eval_rows_bytes"]
    assert 0 < wire < raw, f"白名单没起作用（{raw} → {wire}）"


def test_projected_eval_rows_still_dedupe_on_the_hub(tmp_path: Path) -> None:
    """W3 的另一半：**瘦过**的行拿去 hub 合并两次 ⇒ 行数不变（去重键必须活着）。

    为什么用体里真发出去的那几行：用例自己重抄一份白名单就等于只测了用例自己。
    """
    from worker.eval_rows import append_eval_rows

    root = _make_artifacts(tmp_path / "art", iters=(5,))
    _heavy_eval_log(root, it=5, games=3)
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)
    assert d.sync() == 1
    rows = op.bodies[0]["eval_rows"]

    ledger = tmp_path / "课程" / "eval_log.jsonl"
    assert append_eval_rows(ledger, rows) == 3
    assert append_eval_rows(ledger, rows) == 0, "同一批行并第二次必须一条都不加"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 3


def test_per_game_detail_does_not_ride_the_realtime_body(tmp_path: Path) -> None:
    """Q2：`perGame`/`perGameSecs` 只随抽屉/离线包到达；**本地账本与聚合列照旧**。

    代价（明写在 plan §7.1 Q2 的答案里）：包到之前该轮的逐局弹窗是空的——所以这里还要
    钉住「读数面知道自己摘了几局」（`status()['pergame_deferred']`），否则人只能看见空弹窗。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    per_game = [{"stage": 1, "seed": i, "win": True} for i in range(34)]
    _metrics_row(root, 1, perGame=per_game, perGameSecs=[0.5] * 34, agg={"kl": 0.01})
    before = (root / "metrics.jsonl").read_bytes()
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 1
    (body,) = op.bodies
    assert not ({"perGame", "perGameSecs"} & set(body["row"])), "实时体不许带逐局明细"
    assert body["row"]["agg"] == {"kl": 0.01}, "聚合列照发（控制台指标表靠它）"
    assert (root / "metrics.jsonl").read_bytes() == before, "本地账本不许被摘"
    assert d.status()["pergame_deferred"] == 34


def test_realtime_sends_only_anchor_rounds_and_pending_keeps_every_round(
    tmp_path: Path,
) -> None:
    """W1-C/D：实时体只发**锚点轮**（有 A 层评估读数的那几轮），而 `pending()` 一个字没窄。

    两半都要钉：① 只发 it2；② `pending()` 仍是 [1,3,4]，且抽屉照吃全量（D-3 的安全网）。
    第二半是评审 P0-2 的全部意义——非锚点轮不许「既不上传也不进抽屉」。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3, 4))
    _write_eval_log(root, it=2, games=1)
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op, backup_root=tmp_path / "bk")

    assert d.sync() == 1
    assert [b["it"] for b in op.bodies] == [2]
    assert d.pending() == [1, 3, 4], "非锚点轮必须留在待投集里（抽屉/idle/对账都读它）"
    assert d.status()["pending_anchors"] == 0
    assert d.backup_pending() == 3, "三轮回抽屉（不因不进实时体而失去第二副本）"


def test_final_pass_also_sends_the_newest_non_anchor_round(tmp_path: Path) -> None:
    """D-1 × W1-C/D：段末（`force_probe`）除了锚点，还要把**最新那一轮**带上。

    否则最后一段（距上一个锚点不足 `eval_every`）的进度永远差几轮，续跑要多跑一遍。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3, 4))
    _write_eval_log(root, it=2, games=1)
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync(force_probe=True) == 2
    # 顺序：段末把**最新那一轮**排最前（`_sync` 的 D-1 重排），锚点在后。
    assert [b["it"] for b in op.bodies] == [4, 2]
    assert d.pending() == [1, 3], "段末也不许把非锚点轮从待投集里抹掉"


def test_without_any_eval_rows_every_pending_round_is_sent(tmp_path: Path) -> None:
    """没有锚点可依的腿（不做 A 层评估）**退回全量**——否则它一轮也发不出去。

    这是数据条件不是旋钮：`eval_log.jsonl` 缺失/为空 ⇒ 锚点集为空 ⇒ 「只发锚点」= 零发送。
    """
    root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
    op = _BodyOpener()
    d = _deliverer("http://hub.invalid", root, opener=op)

    assert d.sync() == 3
    assert sorted(b["it"] for b in op.bodies) == [1, 2, 3]

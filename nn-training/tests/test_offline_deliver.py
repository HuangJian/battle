"""tests/test_offline_deliver.py —— 产物**补传**（「中途能连上 hub 就自动恢复在线回传」）。

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_remote_ppo import _boot_server, _http  # type: ignore
from test_run_loop import _FakeRunJob, _prepare, _quiet  # type: ignore

from remote.offline_deliver import OfflineDeliverer
from remote.protocol import (
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESULT_PATH,
    ProtocolError,
    encode_weights_json,
    sanitize_run_id,
)
from remote.run_loop import run_plan_job

RUN = "run-offline-1"


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


def test_hub_unreachable_is_free_and_backlog_delivers_on_reconnect(tmp_path: Path) -> None:
    """**核心需求**：hub 关机期间照跑（返回 0、不抛、不标记）；连上那天积压一次补齐。"""
    root = _make_artifacts(tmp_path / "art")
    down = _deliverer("http://127.0.0.1:1", root)  # 没人听的端口
    assert down.sync() == 0  # 不抛
    assert not (root / "delivered.json").exists()  # 什么都没记成已投递
    assert down.pending() == [1, 2, 3]
    assert down.deliver_result(it_end=3, state="complete") is False

    # 中途连上了（同一目录、同一个 run）
    base, store, srv, th = _boot_server(tmp_path)
    try:
        up = _deliverer(base, root)
        assert up.sync() == 3  # 积压补齐
        assert up.deliver_result(it_end=3, state="complete", summary={"rows": 3}) is True
        rec = json.loads((store.offline_run_dir(RUN) / "result.json").read_text(encoding="utf-8"))
        assert rec["it_end"] == 3 and rec["state"] == "complete"
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


def test_rejected_body_disables_instead_of_retrying(tmp_path: Path) -> None:
    """体被拒（400）重试不会变对：停用并留一行原因，别每轮白烧一个 ~1.9MB upload。"""
    root = _make_artifacts(tmp_path / "art")

    class _Op:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, url: str, data: bytes, headers: dict, timeout: float):
            self.calls.append(url)
            if url.endswith("/ping"):
                return 200, b"{}"
            return 400, b'{"error":"\xe8\xa1\xa5\xe4\xbc\xa0\xe8\xa2\xab\xe6\x8b\x92"}'

    op = _Op()
    d = _deliverer("http://hub.invalid", root, opener=op)
    assert d.sync() == 0
    assert d.disabled_reason != "" and "400" in d.disabled_reason
    assert d.pending() == [1, 2, 3]
    n = len(op.calls)
    d.sync()
    assert len(op.calls) == n


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
    """同一轮权重是不可变快照：重传（重连/重启/重试）不得改写已有产物。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        root = _make_artifacts(tmp_path / "art")
        first = _artifact_body(root, 1)
        assert _http(base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(first).encode())[0] == 200
        w1 = (store.offline_run_dir(RUN) / "it-001" / "weights.json").read_bytes()

        other = dict(first)
        other["weights_json"] = encode_weights_json(b'{"it":1,"w":999}')  # 另一份字节
        import hashlib

        other["weights_fp"] = hashlib.sha256(b'{"it":1,"w":999}').hexdigest()
        status, resp = _http(
            base, "sekret", OFFLINE_ARTIFACT_PATH, "POST", data=json.dumps(other).encode()
        )
        # duplicate 回 200（补传是重试友好的：409 会让节点每轮把已投过的再传一遍）
        assert int(status) == 200 and resp["status"] == "duplicate"
        assert (store.offline_run_dir(RUN) / "it-001" / "weights.json").read_bytes() == w1
        rows = (store.offline_run_dir(RUN) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        assert len([r for r in rows if r.strip()]) == 1  # 账本一行 = 一轮
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
        assert delivered == [1, 2, 3, 4]  # 锚点轮 + 逐轮都到了
        rec = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        assert rec["it_end"] == result["it_end"] == 4 and rec["state"] == "complete"
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
    from remote.offline_deliver import make_deliverer

    d = make_deliverer(
        hub_url="http://hub", hub_token="t", run_id=RUN, artifacts_dir="x", course="c4"
    )
    assert d is not None and d.course == "c4"


def test_run_plan_job_ignores_delivery_failures(tmp_path: Path) -> None:
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
    assert (tmp_path / "art" / "artifacts.zip").exists()  # 产物照常
    assert not (tmp_path / "art" / "delivered.json").exists()  # 一次都没投成

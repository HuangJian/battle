"""「离线 = 发一份 `kind=run` 队列项」这条腿的**退役**回归（2026-09-25）。

背景与裁决：`plan/online-offline-role-routing.plan.md` §7（口径 = §7.0：离线场景里没有
「一整段」这个中间概念——云机接手就一直跑，直到跑完课程 / 配额用尽 / 人工停机停课，能传回
多少是多少）。那条腿（本机发一份带段长的队列项、随后等 8h）与取包链是**同一件事的两个执行者**
——正是 2026-09-25 云机接错盘事故的结构 ⇒ 按裁决退役。

本文件钉四件事（**每条都对应一个静默故障**）：

  ① 生产端只剩一个发布点、且必带 `export_path`（枚举式：第 2 个出现即红）；
  ② 发布咽喉点**当场响亮拒**（不许白等 8h，也不许静默降级成本机采样——那会与云机双跑）；
  ③ 消费端拒收 `kind=run`（且发生在零指令零下载之前）；
  ④ 被接管的课本机循环 = 一行指路 + `ROUND_HELD_EXIT`（不采样、不派发、不进账本、不等待）
     ——★M2：判据从 rl-config 的 `rollout_src=run` 换成 hub 的 hold（`trainer/loop_hold.py`）。

另有两块「离线盘报名」的读数：`/offline/*` 面的盘身份（hub `offline_disk` 读数）与
`offline_boot` 的自报头 —— 没有它，「本环境有没有离线盘」永远是无从回答的（审计 §4-L3）。
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

from common.protocol import (
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
    ProtocolError,
)
from hub.server import _HubQueue, make_server
from remote import worker as W
from tests.hub.test_role_routing import OFF_JID, _manifest, _store  # type: ignore
from tests.remote.test_worker_bun_precheck import _job as _worker_job  # type: ignore
from trainer.loop_round_steps import RoundSteps
from trainer.loop_steps import TrainingSteps
from worker.loop_round import COLLECT_HELD, COLLECT_LOCAL, ROUND_HELD_EXIT, RoundContext

TOKEN = "sekret"
# 2026-09-30（刀 4）：`biz/` 是 `rl/` 的纯逻辑半（搬家前就在扫描面里）⇒ 必须补上，
# 否则本文件的「发布点 / 导出路径」枚举判据会静默少扫 64 个模块。
# （「已退役标识不得回流」的本尊已改为单点 `tests/retired_contracts.py`，它的扫描面自带基线守卫。）
PROD_DIRS = ("trainer", "biz", "remote")
#: 根目录上的两块生产代码（与 `trainer/`、`biz/` 同级，别漏）。
PROD_FILES = ("trainer/run_rl.py", "common/distribution.py")


def _prod_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for sub in PROD_DIRS:
        for f in sorted((ROOT / sub).rglob("*.py")):
            out[str(f.relative_to(ROOT)).replace("\\", "/")] = f.read_text(encoding="utf-8")
    for name in PROD_FILES:
        p = ROOT / name
        if p.exists():
            out[name] = p.read_text(encoding="utf-8")
    return out


def _call_args(src: str, start: int) -> str:
    """取一次调用的完整实参文本（括号配平）——用来断言「这一跳带了什么」。"""
    i = src.index("(", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    raise AssertionError("调用括号不配平（源码被改动过？）")


# ═══════════════════════ ① 生产端：只有一个发布点，且必带 export_path ═══════════════════════
#
# ⓘ 2026-10-09：原来这里还有一条 `test_production_code_has_no_trace_of_the_retired_leg`
# （全仓扫源码、断言「`_remote_run_segment` / `run_wait_sec` 这些名字不在生产代码里」）。
# 它已迁进**单点** `tests/retired_contracts.py`（清单 `RETIRED_CONTRACTS` + 唯一扫描器
# `scan()`；驱动 `tests/test_retired_contracts.py`）——plan/nn-training-test-debt-cleanup.plan.md
# §2-T2：同一件事的措辞散在 8 个文件里，下次退役必然漏同步。别照着重写一份。


def test_run_manifest_is_published_from_exactly_one_place_with_export_path() -> None:
    """`kind=run` 的发布**只有一处**、且必带 `export_path`（= 任务包导出，不进待领池）。

    枚举式：加第 2 个调用点时这里红，而实施者必须回答一个问题——「这份活是给**队列**的
    还是给**任务包**的？」。前者是本次事故的结构（一个任务两个执行者），后者才是今天的形状。
    行为面的兜底在 `test_publish_choke_point_refuses_a_run_queue_job_loudly`。
    """
    # 三处家随本地拆分（S4 第二十一/二十二刀）分散了：
    #   · **生产点**（交出计划那一处）：`trainer/loop_export.py::_export_offline_bundle`；
    #   · **转发跳**（`_remote_ppo` → `_remote_ppo_publish`）：`trainer/loop_remote_job.py`；
    #   · **真发布点**（`publish_job`）：同一个 `trainer/loop_remote_job.py`。
    # 继续只读 `trainer/loop_steps.py` 会让这条守卫**静默空过**（那里已经没有调用点）。
    prod = (ROOT / "trainer" / "loop_export.py").read_text(encoding="utf-8")
    producers = [
        _call_args(prod, m.start())
        for m in re.finditer(r"self\._remote_ppo\(", prod)
        if "plan_bytes" in _call_args(prod, m.start())
    ]
    # ① 「源点」= 真的**交出一份计划**的调用（它就是唯一那个带字面实参的调用）。
    assert len(producers) == 1, f"交出计划的发布点有 {len(producers)} 处（期望 1）：{producers}"
    assert "export_path" in producers[0], f"唯一发布点没带 export_path ⇒ 复活了队列腿：{producers[0]}"
    # ② 转发跳（`_remote_ppo` → `_remote_ppo_publish`）：两件都必须原样带上，少带一件
    #    就在咽喉点被拒（那是**响亮**的，但仍要钉住形状，免得靠「能跑」反推）。
    job = (ROOT / "trainer" / "loop_remote_job.py").read_text(encoding="utf-8")
    forwards = [
        _call_args(job, m.start())
        for m in re.finditer(r"self\._remote_ppo_publish\(", job)
        if "plan_bytes=plan_bytes" in _call_args(job, m.start())
    ]
    assert len(forwards) == 1 and "export_path=export_path" in forwards[0], forwards

    # `publish_job(kind=run, plan_bytes=…)` 也只有这一处（它是上面那个发布点的内部跳）。
    calls = [
        rel
        for rel, s in _prod_sources().items()
        for m in re.finditer(r"(?<!def )\bpublish_job\(", s)  # 排除函数定义那一行
        if "plan_bytes" in _call_args(s, m.start())
    ]
    assert calls == ["trainer/loop_remote_job.py"], f"多出一个造 kind=run manifest 的生产点：{calls}"


def test_publish_choke_point_refuses_a_run_queue_job_loudly(tmp_path: Path) -> None:
    """发布咽喉点：`plan_bytes` 而无 `export_path` ⇒ SystemExit，且**指路取包链**。

    为什么必须是**当场**拒：老行为是发出去、然后等 8h（`RUN_WAIT_DEFAULT_SEC`），而队列里
    已经没有消费者（`worker.py` 拒收 kind=run）⇒ 本机会静默白等一整段。症状（队列不降、
    没人报错）恰恰是最难查的那类。
    """
    steps: Any = TrainingSteps.__new__(TrainingSteps)
    steps.args = SimpleNamespace()
    steps._traj_dir = tmp_path
    with pytest.raises(SystemExit) as ei:
        steps._remote_ppo_publish(1, None, plan_bytes=b"{}", export_path=None)
    msg = str(ei.value)
    assert "退役" in msg and "battle.offline.ipynb" in msg, msg
    assert "--export-bundle" in msg, "拒绝消息必须给出本机此刻该走的那条路（任务包）"


# ═══════════════════════ ③ 消费端：worker 拒收 kind=run ═══════════════════════


def test_worker_refuses_a_run_kind_job_before_any_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worker 侧的确定性拒绝（同一个消息指路），且发生在**取 payload 之前**。

    拒绝而不是「当成 iter 跑一轮」：半跑一轮会产出权重、让控制面看着像在推进，而 hub 侧
    早就不等了 —— 那正是 plan §7.2 要治的静默分叉。
    """
    download_calls: list[str] = []

    def _spy(_base: str, _token: str, jid: str, **_kw: object) -> bytes:
        download_calls.append(jid)
        raise AssertionError("不该下载：kind=run 必须在零下载之前被拒")

    monkeypatch.setattr(W, "download_payload", _spy)
    with pytest.raises(ProtocolError) as ei:
        W.run_job("http://hub", "tok", _worker_job("run"), work_dir=tmp_path)
    assert download_calls == []
    assert "battle.offline.ipynb" in str(ei.value), str(ei.value)


# ═════════════════ ④ 本机循环：被接管的课不跑（一行指路 + 干净收官；★M2 换判据） ═════════════════


def _bare_steps(*, held: bool = True) -> Any:
    """不跑 `__init__` 的 `RoundSteps`：只喂 `step_course_iter` 真正会碰到的那几样。

    `Any` 是刻意的（与 `tests/worker/test_loop_round.py::_bare_loop` 同一手法）：本用例的**目的**
    就是「这一步在被接管的课上做了什么」，替身不是被测对象。

    `held` 旗子由调用方经 `lrs.course_held` 的 monkeypatch 注入（★M2 的双通道判据不在本文件
    的测程里——它有自己的用例，见 `tests/trainer/test_loop_hold.py`）。
    """
    steps: Any = RoundSteps()
    steps.args = SimpleNamespace(
        workers=0,
        local_slots=0,
        rollout_src="auto",
        run_iters=-1,
        export_bundle="",
        course_path="curricula/x1.jsonc",
    )
    steps._total = 9
    steps._node_rollout = False
    steps._course_iter = lambda _it: None
    steps._iteration_pairs = lambda _it: []
    steps._evalboard_yield = lambda: None
    steps._eval_on_round = lambda _it: False
    return steps


@pytest.mark.parametrize("run_iters", [-1, 3, 0])
def test_held_course_stops_the_round_cleanly_with_a_pointing_line(
    monkeypatch: pytest.MonkeyPatch, run_iters: int
) -> None:
    """被接管（`held`）⇒ 一行指路 + `ROUND_HELD_EXIT`；**不**回落本机采样。

    段长三种值都在内（★M2 后它与「归谁」无关）：回落 `COLLECT_LOCAL` 的代价是本机偷偷自己
    采样、与云机取包链**双跑**（plan §7.2-1 那个坑）。
    """
    import trainer.loop_round_steps as lrs

    lines: list[str] = []
    monkeypatch.setattr(lrs, "log", lines.append)
    monkeypatch.setattr(lrs.common.distribution, "load_dist_config", lambda: {})
    monkeypatch.setattr(lrs, "course_held", lambda _args, **_kw: True)
    exported: list[tuple] = []
    steps = _bare_steps()
    steps.args.run_iters = run_iters
    steps._export_offline_bundle = lambda *a: exported.append(a)
    ctx = RoundContext(it=4, pairs=[])

    out = steps.step_course_iter(ctx)

    assert out is not None and out.is_final and out.outcome == ROUND_HELD_EXIT
    assert ctx.collect_mode == COLLECT_HELD
    assert ctx.node_rollout is True, "被接管的课也在「不采样」那一档（别让它掉回本机）"
    assert any("battle.offline.ipynb" in ln for ln in lines), lines
    assert any("被接管" in ln for ln in lines), lines
    assert exported == [], "没给 --export-bundle 就不该走导出（那是另一条腿）"


def test_not_held_keeps_the_local_leg_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """负对照：`held=False` ⇒ 一切旧行为（`local` + 轮子继续走）；卷轴不因「问了一次 hub」而变。"""
    import trainer.loop_round_steps as lrs

    monkeypatch.setattr(lrs.common.distribution, "load_dist_config", lambda: {})
    monkeypatch.setattr(lrs, "course_held", lambda _args, **_kw: False)
    steps = _bare_steps()
    ctx = RoundContext(it=4, pairs=[])
    assert steps.step_course_iter(ctx) is None
    assert ctx.collect_mode == COLLECT_LOCAL
    assert steps._node_rollout is False


def test_export_bundle_still_wins_over_the_held_early_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--export-bundle` 早退仍在（取包链的入口）：它先跑，再轮到接管的收官。

    顺序是被钉住的：导出腿是**唯一**合法的 kind=run 形状，若被接管早退挡在前面，
    控制台「切离线」的自动导出会静默什么都不做（而那次导包正是云机 claim 的前提）。
    """
    import trainer.loop_round_steps as lrs

    monkeypatch.setattr(lrs, "log", lambda _m: None)
    monkeypatch.setattr(lrs.common.distribution, "load_dist_config", lambda: {})
    monkeypatch.setattr(lrs, "course_held", lambda _args, **_kw: True)
    exported: list[tuple] = []
    steps = _bare_steps()
    steps.args.export_bundle = "tools.task.zip"
    steps._export_offline_bundle = lambda *a: exported.append(a)
    out = steps.step_course_iter(RoundContext(it=2, pairs=[]))
    assert len(exported) == 1
    assert out is not None and out.outcome == ROUND_HELD_EXIT


def test_export_refusal_rejects_a_course_that_already_reached_iters() -> None:
    """★ 2026-10-07 现场（k5 跑到 it151，课程声明 iters=150，hub 反复触发导出却永远拿不到包）：

    `--export-bundle` 的导出分支只住在轮内（`step_course_iter`，上面两例钉着它），而课跑满
    之后 `run()` 的轮体**一次都不进** ⇒ 旧行为是**静默不导**：日志只有 ALL DONE + 收官
    drain（一场 ~1 小时的逐检查点 eval），任务包永远不出现，「导包中」永远挂着。
    启动期必须**响亮拒导**（裸 `[run_rl]` 行 ⇒ 控制台的 exit-watchdog 能把它当退出原因）。
    """
    from trainer.loop_lifecycle import export_refusal

    msg = export_refusal(export_path="tmp/x/task-x.zip", iters_total=150, start_it=151)
    assert msg.startswith("[run_rl] --export-bundle"), msg
    assert "it150" in msg and "iters=150" in msg, msg
    # 还有轮次可跑（start_it == iters 时轮体仍进得去）⇒ 不拒
    assert export_refusal(export_path="tmp/x/task-x.zip", iters_total=150, start_it=150) == ""
    assert export_refusal(export_path="tmp/x/task-x.zip", iters_total=150, start_it=1) == ""
    # 非导出路（普通训练）不看这条判据
    assert export_refusal(export_path="", iters_total=150, start_it=9999) == ""
    # 缺终点（iters<=0）走与轮内那条同口径的文案
    assert "必须有终点" in export_refusal(export_path="tmp/x/task-x.zip", iters_total=0, start_it=1)


def test_held_course_never_reaches_the_collect_step() -> None:
    """采集步对被接管的课**响亮报错**：真走到那里 = 步骤顺序被改动过，静默退化的代价是双跑。"""
    steps = _bare_steps()
    ctx = RoundContext(it=1, pairs=[])
    ctx.collect_mode = COLLECT_HELD
    with pytest.raises(RuntimeError, match="被接管"):
        steps.step_rollout(ctx)


def test_held_round_maps_to_waiting_not_a_false_finish() -> None:
    """★P1-1（plan §3.5）：调度层把 `ROUND_HELD_EXIT` 映射成 **WAIT**，不是 DONE。

    旧写法 `done(final=True)` 把被接管的课当「已收官」：控制台显示完结、写假 `run_complete`
    （而云机那边还在跑，R1-e），且本轮从队列里消失 ⇒ 接管解除也不会自动续跑。
    WAIT + `hold=False` 才是对的：本机没在替这一步干活，这一轮留着、下一拍重问。
    """
    from trainer.loop_runner import LoopRunner
    from worker.loop_round import RoundOutcome
    from worker.loop_tasks import WAIT

    runner = LoopRunner(
        loop=SimpleNamespace(inflight_job_id=lambda _it: None), course="c5-gae"
    )
    res = runner._map_outcome(RoundOutcome(ROUND_HELD_EXIT, 4))
    assert res.status == WAIT, res
    assert res.hold is False, "接管不是「后台还在干活」，票要还掉"
    assert res.payload.get("round") == "4", res.payload
    assert "接管" in res.reason, res.reason
    assert runner.finished is False and runner.finish_reason == ""


def test_retired_run_source_is_tolerated_not_bricked(monkeypatch: pytest.MonkeyPatch) -> None:
    """★M2（plan §3-M2）：旧配置里的 `run` **容忍读**——映射成本机 + 一行 WARN。

    为什么不能拒启：一份残留配置（控制台旧版本写的，或人手改的）会让那门课永远停在原地，
    而日志里只有一个「未知取值」——那正是「配置删字段」那个坑的镜像。
    """
    from common import log as common_log
    from trainer import loop_transport as lt

    lines: list[str] = []
    monkeypatch.setattr(lt, "_RETIRED_SOURCE_NOTED", set())
    monkeypatch.setattr(common_log, "log", lines.append)

    # CLI 与 rl-config 两条入口都要容忍（一条拒启就够 brick 一门课）
    assert lt._rollout_source(SimpleNamespace(rollout_src="run", course_path="")) == "local"
    assert any("已退役" in ln for ln in lines), lines
    n = len(lines)
    assert lt._rollout_source(SimpleNamespace(rollout_src="run", course_path="")) == "local"
    assert len(lines) == n, "同一（课, 值）只喊一次：`_rollout_source` 每轮都被问"

    monkeypatch.setattr(
        lt.common.distribution,
        "load_dist_config",
        lambda: {"courses": {"c5-gae": {"rollout_src": "run"}}},
    )
    assert (
        lt._rollout_source(SimpleNamespace(rollout_src="auto", course_path="curricula/c5-gae.jsonc"))
        == "local"
    )
    assert any("c5-gae" in ln and "已退役" in ln for ln in lines), lines
    # 枚举本身不再含它（生产端不许把它当合法档传下去）
    assert "run" not in lt.ROLLOUT_SRCS and "run" in lt.ROLLOUT_SRCS_RETIRED


def test_explicit_rollout_src_declares_restore_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★P1-1（plan §3.5）：显式 `--rollout-src node` 会短路 rl-config ⇒ 「控制台改回本机采样
    后自动续跑」**不支持**——必须明说（隐式依赖不得静默失败；否则课程永远等下去）。

    ★M2：这条契约现在只剩 `node`（`run` 已退役、走 `_note_retired_source` 那一条路）。
    同一（课, 实测值）去重——`_rollout_source` 每轮都会被问一次。
    """
    from common import log as common_log
    from trainer import loop_transport as lt

    lines: list[str] = []
    monkeypatch.setattr(lt, "_RESTORE_UNSUPPORTED_NOTED", set())
    monkeypatch.setattr(common_log, "log", lines.append)

    args = SimpleNamespace(rollout_src="node", run_iters=0, course_path="curricula/c5-gae.jsonc")
    assert lt._rollout_source(args) == "node"
    assert any("不支持" in ln for ln in lines), lines
    n = len(lines)
    assert lt._rollout_source(args) == "node"  # 去重：不再刷第二行
    assert len(lines) == n
    # `local` 与这条契约无关：不喊
    assert (
        lt._rollout_source(SimpleNamespace(rollout_src="local", run_iters=0, course_path=""))
        == "local"
    )
    assert len(lines) == n


# ═══════════════════════ ⑤ 读数：离线盘报名 + 「没人能领的离线项」 ═══════════════════════


def test_hub_readout_names_the_unclaimable_offline_residue(tmp_path: Path) -> None:
    """盘上遗留的离线队列项必须被**点名**（`stale_jobs` + 一行指路），而不是躺着没人知道。

    这类项今天只可能来自「盘上遗留 / 手写参数 / 混部期旧 hub」——本机已不再发它、也不再等它。
    """
    store = _store(tmp_path)
    store.publish(OFF_JID, _manifest(OFF_JID, kind="run"), b"PK\x03\x04fake")
    hub = _HubQueue({"c5-gae": store}, order=["c5-gae"])

    r = hub.offline_disk_readout()
    assert r["recent_n"] == 0 and r["last_seen_ago"] is None, "还没人报过名"
    assert r["stale_jobs"] == [{"course": "c5-gae", "job_id": OFF_JID}]
    assert "battle.offline.ipynb" in r["hint"]

    hub.note_offline_disk("kaggle-1")
    r2 = hub.offline_disk_readout()
    assert r2["recent"] == ["kaggle-1"] and r2["recent_n"] == 1
    assert r2["last_seen_ago"] is not None
    assert r2["stale_jobs"] == r["stale_jobs"], "报名不改遗留项的归属（它仍没人能领）"


def test_queue_state_carries_the_offline_disk_readout(tmp_path: Path) -> None:
    """`/admin/queue` 的体里必须有它（否则读数只活在测试里 = 没人看得见）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    assert "offline_disk" in hub.queue_state()


def test_offline_face_registers_the_disk_identity(tmp_path: Path) -> None:
    """`/offline/*` 面的**报名**：带角色头 ⇒ 记一次；不带 ⇒ 不记（负对照同用例）。

    这是「本环境有没有离线盘」唯一的报到面：跑 `battle.offline.ipynb` 的机器不碰 `/jobs/*`。
    """
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _get(base, "/offline/tasks", {ROLE_HEADER: ROLE_HEADER_VALUE})
        _get(base, "/offline/claim?course=c5-gae&worker=disk-1", {ROLE_HEADER: ROLE_HEADER_VALUE})
        _get(base, "/offline/tasks", {})  # 负对照：不带角色头（老盘/老客户端）
        assert hub.offline_disk_readout()["recent"] == ["<offline>", "disk-1"]
    finally:
        srv.shutdown()
        srv.server_close()


def _get(base: str, path: str, headers: dict[str, str]) -> int:
    """裸 GET（只要状态码；403/404 都无所谓——报名发生在业务处理之前）。"""
    h = {"Authorization": "Bearer " + TOKEN}
    h.update(headers)
    req = urllib.request.Request(base + path, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:  # 业务拒绝不影响「报名」这件事
        return int(e.code)


# ═══════════════════════ ⑥ 云机自报：offline_boot 的盘身份 ═══════════════════════


def test_offline_boot_announces_the_disk_identity_on_every_hub_call() -> None:
    """`offline_boot` 的每个 hub 调用都带 `X-Battle-Offline`（漏一处 = 那一次没报名）。

    为什么在这里钉「不许有裸 Authorization 字面量」：本模块**不得 import `remote.*`**
    （包到手之前那个包还不存在，见其文件头），所以头名/值只能各留一份拷贝——「逐字相同」
    因此必须由测试守。漏掉的那个调用点，症状是**静默**的（读数少一次、包可能被别处拿走）。
    """
    import remote.offline_boot as OB

    assert OB.ROLE_HEADER == ROLE_HEADER, "跨层契约：头名必须与 protocol 逐字相同"
    assert OB.ROLE_HEADER_VALUE == ROLE_HEADER_VALUE
    h = OB._headers("tok")
    assert h[ROLE_HEADER] == ROLE_HEADER_VALUE and h["Authorization"] == "Bearer tok"

    src = (ROOT / "remote" / "offline_boot.py").read_text(encoding="utf-8")
    assert '{"Authorization": "Bearer " + token}' not in src, "有调用点绕过了 `_headers`"

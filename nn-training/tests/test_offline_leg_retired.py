"""「离线 = 发一份 `kind=run` 队列项」这条腿的**退役**回归（2026-09-25）。

背景与裁决：`plan/online-offline-role-routing.plan.md` §7（口径 = §7.0：离线场景里没有
「一整段」这个中间概念——云机接手就一直跑，直到跑完课程 / 配额用尽 / 人工停机停课，能传回
多少是多少）。那条腿（本机发一份带段长的队列项、随后等 8h）与取包链是**同一件事的两个执行者**
——正是 2026-09-25 云机接错盘事故的结构 ⇒ 按裁决退役。

本文件钉四件事（**每条都对应一个静默故障**）：

  ① 生产端只剩一个发布点、且必带 `export_path`（枚举式：第 2 个出现即红）；
  ② 发布咽喉点**当场响亮拒**（不许白等 8h，也不许静默降级成本机采样——那会与云机双跑）；
  ③ 消费端拒收 `kind=run`（且发生在零指令零下载之前）；
  ④ 离线课本机循环 = 一行指路 + `ROUND_OFFLINE_EXIT`（不采样、不派发、不进账本、不等待）。

另有两块「离线盘报名」的读数：`/offline/*` 面的盘身份（hub `offline_disk` 读数）与
`offline_boot` 的自报头 —— 没有它，「本环境有没有离线盘」永远是无从回答的（审计 §4-L3）。
"""

from __future__ import annotations

import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 跨测试文件 import（同 test_offline_deliver）
ROOT = Path(__file__).resolve().parent.parent

from test_role_routing import OFF_JID, _manifest, _store  # type: ignore
from test_worker_bun_precheck import _job as _worker_job  # type: ignore

from remote import worker as W
from remote.hub_server import _HubQueue, make_server
from remote.protocol import (
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
    ProtocolError,
)
from rl.loop_round import COLLECT_OFFLINE, ROUND_OFFLINE_EXIT, RoundContext
from rl.loop_round_steps import RoundSteps
from rl.loop_steps import TrainingSteps

TOKEN = "sekret"
PROD_DIRS = ("rl", "remote")
#: 根目录上的两块生产代码（与 `rl/` 同级，别漏）。
PROD_FILES = ("run_rl.py", "dist_common.py")


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

#: 退役腿的**标识**：这些名字回到生产代码里就等于那条腿复活（`run_wait_sec` 是 8h 白等，
#: `_remote_run_segment` 是发布端本体，另外两个是它的读数/入口）。
_RETIRED_TOKENS = (
    "_remote_run_segment",
    "RUN_WAIT_DEFAULT_SEC",
    "_run_wait_sec",
    "run_wait_sec",
)


def test_production_code_has_no_trace_of_the_retired_leg() -> None:
    """退役腿的标识在生产代码里**零命中**（`run` 作为来源枚举值留着，见本文件 ④）。"""
    hits: list[str] = []
    for rel, src in _prod_sources().items():
        for tok in _RETIRED_TOKENS:
            if tok in src:
                hits.append(f"{rel}: {tok}")
    assert hits == [], f"退役腿的标识又出现在生产代码里：{hits}（plan §7.6 的退役清单）"


def test_run_manifest_is_published_from_exactly_one_place_with_export_path() -> None:
    """`kind=run` 的发布**只有一处**、且必带 `export_path`（= 任务包导出，不进待领池）。

    枚举式：加第 2 个调用点时这里红，而实施者必须回答一个问题——「这份活是给**队列**的
    还是给**任务包**的？」。前者是本次事故的结构（一个任务两个执行者），后者才是今天的形状。
    行为面的兜底在 `test_publish_choke_point_refuses_a_run_queue_job_loudly`。
    """
    src = (ROOT / "rl" / "loop_steps.py").read_text(encoding="utf-8")
    calls = [
        _call_args(src, m.start())
        for m in re.finditer(r"self\._remote_ppo(?:_publish)?\(", src)
        if "plan_bytes" in _call_args(src, m.start())
    ]
    # ① 「源点」= 真的**交出一份计划**的调用（转发跳只把形参原样往下传，见②）。
    producers = [t for t in calls if "plan_bytes=plan_bytes" not in t]
    assert len(producers) == 1, f"交出计划的发布点有 {len(producers)} 处（期望 1）：{producers}"
    assert "export_path" in producers[0], f"唯一发布点没带 export_path ⇒ 复活了队列腿：{producers[0]}"
    # ② 转发跳（`_remote_ppo` → `_remote_ppo_publish`）：两件都必须原样带上，少带一件
    #    就在咽喉点被拒（那是**响亮**的，但仍要钉住形状，免得靠「能跑」反推）。
    forwards = [t for t in calls if "plan_bytes=plan_bytes" in t]
    assert len(forwards) == 1 and "export_path=export_path" in forwards[0], forwards

    # `publish_job(kind=run, plan_bytes=…)` 也只有这一处（它是上面那个发布点的内部跳）。
    calls = [
        rel
        for rel, s in _prod_sources().items()
        for m in re.finditer(r"(?<!def )\bpublish_job\(", s)  # 排除函数定义那一行
        if "plan_bytes" in _call_args(s, m.start())
    ]
    assert calls == ["rl/loop_steps.py"], f"多出一个造 kind=run manifest 的生产点：{calls}"


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


# ═══════════════════════ ④ 本机循环：离线课不跑（一行指路 + 干净收官） ═══════════════════════


def _bare_steps() -> Any:
    """不跑 `__init__` 的 `RoundSteps`：只喂 `step_course_iter` 真正会碰到的那几样。

    `Any` 是刻意的（与 `tests/test_loop_round.py::_bare_loop` 同一手法）：本用例的**目的**
    就是「这一步在离线课上做了什么」，替身不是被测对象。
    """
    steps: Any = RoundSteps()
    steps.args = SimpleNamespace(
        workers=0,
        local_slots=0,
        rollout_src="run",
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
def test_offline_course_stops_the_round_cleanly_with_a_pointing_line(
    monkeypatch: pytest.MonkeyPatch, run_iters: int
) -> None:
    """离线课（`rollout_src=run`）⇒ 一行指路 + `ROUND_OFFLINE_EXIT`；**不**回落本机采样。

    `run_iters=0`（只写了来源、没写段长）也在内：回落 `COLLECT_LOCAL` 的代价是本机偷偷自己
    采样、与云机取包链**双跑**（plan §7.2-1 那个坑），所以「来源是 run」就足够判离线。
    """
    import rl.loop_round_steps as lrs

    lines: list[str] = []
    monkeypatch.setattr(lrs, "log", lines.append)
    monkeypatch.setattr(lrs.dist_common, "load_dist_config", lambda: {})
    exported: list[tuple] = []
    steps = _bare_steps()
    steps.args.run_iters = run_iters
    steps._export_offline_bundle = lambda *a: exported.append(a)
    ctx = RoundContext(it=4, pairs=[])

    out = steps.step_course_iter(ctx)

    assert out is not None and out.is_final and out.outcome == ROUND_OFFLINE_EXIT
    assert ctx.collect_mode == COLLECT_OFFLINE
    assert ctx.node_rollout is True, "离线课也在「不采样」那一档（别让它掉回本机）"
    assert any("battle.offline.ipynb" in ln for ln in lines), lines
    assert exported == [], "没给 --export-bundle 就不该走导出（那是另一条腿）"


def test_export_bundle_still_wins_over_the_offline_early_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--export-bundle` 早退仍在（取包链的入口）：它先跑，再轮到离线课的收官。

    顺序是被钉住的：导出腿是**唯一**合法的 kind=run 形状，若被离线早退挡在前面，
    控制台「切离线」的自动导出会静默什么都不做。
    """
    import rl.loop_round_steps as lrs

    monkeypatch.setattr(lrs, "log", lambda _m: None)
    monkeypatch.setattr(lrs.dist_common, "load_dist_config", lambda: {})
    exported: list[tuple] = []
    steps = _bare_steps()
    steps.args.export_bundle = "task.zip"
    steps._export_offline_bundle = lambda *a: exported.append(a)
    steps.step_course_iter(RoundContext(it=2, pairs=[]))
    assert len(exported) == 1


def test_offline_course_never_reaches_the_collect_step() -> None:
    """采集步对离线课**响亮报错**：真走到那里 = 步骤顺序被改动过，而静默退化的代价是双跑。"""
    steps = _bare_steps()
    ctx = RoundContext(it=1, pairs=[])
    ctx.collect_mode = COLLECT_OFFLINE
    with pytest.raises(RuntimeError, match="离线课"):
        steps.step_rollout(ctx)


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

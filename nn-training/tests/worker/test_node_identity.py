"""test_node_identity — 「同 node 的 bootId 与本轮账本不一致 ⇒ 本轮排除」的训练侧判据。

触发：a98（2026-09-29）一台机器上两个 agent 同时听 8443，内核按 4 元组哈希把一轮 192 局的请求
分给两个代码版本 ⇒ 45 列的旧 shard 混进 payload ⇒ 云 worker `grad` 5 连炸、整门课 aborted
（plan/sampler-single-instance.plan.md §1.1/§8-Q2）。agent 侧的应用层互斥是第一道防线；
本文件钉训练侧那道**客户端观测**判据：`/v1/ping` 的 `pid`/`bootId` 在同一轮里变了。

三个必须钉死的行为面（判据的边界）：
  ① 旧 agent **没有** `bootId` 字段 ⇒ 恒无意见（fail-open）——升级波期间不得把没升的节点排除；
  ② 钉子**只钉一次**（不一致后不改写）：不一致是**本轮**的事实，重复 ping 幂等；
  ③ 按**轮**记账（`round_key(leg, ident)`）——一次正常升级/重启换 bootId 不得跨轮误伤。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from worker import node_identity as ni


@pytest.fixture(autouse=True)
def _isolated_ledger():
    """每个用例一份干净账本（`worker.node_identity._LEDGER` 是进程级持有者 ⇒ 必须显式隔离）。"""
    ni.clear()
    yield
    ni.clear()


def _ping(boot: str = "aaaaaaaa", pid: int = 100) -> dict:
    """一条 `/v1/ping` 响应（只留判据要的字段）。"""
    return {"ok": True, "pid": pid, "bootId": boot, "codeHash": "f" * 64}


def test_first_ping_pins_and_same_bootid_stays_clean() -> None:
    rkey = ni.round_key("rollout", "it1")
    assert ni.note_ping(rkey, "a98", _ping()) is None  # 第一次：钉
    assert ni.note_ping(rkey, "a98", _ping()) is None  # 同进程再 ping：一致


def test_bootid_change_is_reported_with_both_sides() -> None:
    rkey = ni.round_key("rollout", "it1")
    ni.note_ping(rkey, "a98", _ping(boot="c3cc5d88", pid=26105))
    why = ni.note_ping(rkey, "a98", _ping(boot="7c9bc324", pid=12841))
    assert why is not None
    assert "c3cc5d88" in why and "7c9bc324" in why  # 两侧都点出来（归因要能一眼看出哪台换过）
    assert "26105" in why and "12841" in why
    assert "两个 agent" in why  # 指路：这是「一个端口两个进程」，不是普通故障


def test_pin_is_never_rewritten_so_repeats_are_idempotent() -> None:
    rkey = ni.round_key("rollout", "it1")
    ni.note_ping(rkey, "a98", _ping(boot="first000", pid=1))
    assert ni.note_ping(rkey, "a98", _ping(boot="second00", pid=2)) is not None
    third = ni.note_ping(rkey, "a98", _ping(boot="third000", pid=3))
    # 仍是「与首个钉子比」——否则第三次会被写成「与 second 一致」而静默转绿
    assert third is not None
    assert "first000" in third


def test_old_agent_without_bootid_is_fail_open_and_leaves_no_pin() -> None:
    rkey = ni.round_key("rollout", "it1")
    for legacy in ({"ok": True}, {"ok": True, "bootId": ""}, {"ok": True, "bootId": None}):
        assert ni.note_ping(rkey, "old", legacy) is None
    # 「无字段」不等于「钉在空值」：随后首次带 bootId 的那次才是钉子
    assert ni.note_ping(rkey, "old", _ping(boot="aaaa")) is None
    assert ni.note_ping(rkey, "old", _ping(boot="bbbb")) is not None


def test_ping_none_or_bad_shape_never_raises_and_never_pins() -> None:
    rkey = ni.round_key("rollout", "it1")
    bads: tuple[object, ...] = (None, "nope", 3, [], {})
    for bad in bads:
        assert ni.note_ping(rkey, "n", bad) is None  # type: ignore[arg-type]
    assert ni.note_ping(rkey, "n", _ping(boot="aaaa")) is None  # 坏输入没留钉子


def test_rounds_are_isolated_so_a_restart_between_rounds_is_not_harmful() -> None:
    it1 = ni.round_key("rollout", "it1")
    it2 = ni.round_key("rollout", "it2")
    ni.note_ping(it1, "a98", _ping(boot="gen1", pid=1))
    # 下一轮（节点在这里合法重启过）⇒ 重新钉，不得报 stale
    assert ni.note_ping(it2, "a98", _ping(boot="gen2", pid=2)) is None
    assert ni.note_ping(it2, "a98", _ping(boot="gen2", pid=2)) is None


def test_round_key_carries_the_leg_so_rollout_and_eval_do_not_share_a_ledger() -> None:
    assert ni.round_key("rollout", "it1") != ni.round_key("eval", "it1")
    assert ni.round_key("eval", "it1") == "it1:eval"


def test_ledger_is_bounded_to_the_recent_rounds() -> None:
    keys = [f"it{i}" for i in range(ni.LEDGER_MAX_ROUNDS + 1)]
    for i, k in enumerate(keys):
        assert ni.note_ping(k, "a98", _ping(boot=f"gen{i}", pid=i)) is None
    # 最旧那轮已被挤掉 ⇒ 重新钉（返回 None），不报「变了」
    assert ni.note_ping(keys[0], "a98", _ping(boot="newgen", pid=99)) is None
    # 最近那轮仍记得：同一轮内换 bootId 照样报
    assert ni.note_ping(keys[-1], "a98", _ping(boot="other", pid=98)) is not None


# ── 接线守卫（网关点必须真调这个判据；钉与查必须共用同一个 round key）────────────────

#: 2026-11-20：批评估的节点门（`bringup` 的回场口）随通道机器搬去 `batch_lanes.py`。
GATE_SITES = ("dispatch_state.py", "queue_local.py", "eval_dispatch.py", "batch_lanes.py")


def _src(name: str) -> str:
    return (ROOT / "trainer" / name).read_text(encoding="utf-8")


def test_every_node_gate_site_consults_the_bootid_ledger() -> None:
    missing = [n for n in GATE_SITES if "node_identity.note_ping(" not in _src(n)]
    assert not missing, (
        f"这些节点门漏了 bootId 判据：{missing} —— 节点门 = codeHash ∧ bun ∧（新）本轮 bootId 一致；"
        "漏一处就是「a98 同类事故在这条腿上无人看见」"
    )


def test_rollout_pin_and_check_share_one_round_key_expression() -> None:
    """钉在 `dispatch_state.py`、查在 `queue_local.py`——两处各写一份字面量必漂（漂了就永不报警）。"""
    pat = re.compile(r'node_identity\.round_key\("rollout",\s*([\w.]+)\)')
    pin = pat.findall(_src("dispatch_state.py"))
    check = pat.findall(_src("queue_local.py"))
    assert pin and check, f"rollout 的 round key 调用点没找到（pin={pin} check={check}）"
    assert set(pin) == set(check), (
        f"pin/check 的 ident 不一致：{pin} vs {check} —— 共用账本键是这条判据成立的前提"
    )


# ── 账本自身（P0-5，2026-11-21：`_LEDGER` 裸 dict → `BootLedger`）────────────────

def test_ledger_is_one_named_holder_and_module_keeps_no_bare_dict() -> None:
    """模块级只有 `_LEDGER` 这个显式持有者，没有裸 dict（判据的状态必须有一个名字）。"""
    holders = {
        name for name, value in vars(ni).items() if isinstance(value, ni.BootLedger)
    }
    assert holders == {"_LEDGER"}, f"账本持有者应当只有一个：{sorted(holders)}"
    bare = {
        name
        for name, value in vars(ni).items()
        if not name.startswith("__")
        and isinstance(value, (list, dict, set))
        and getattr(value, "__module__", None) == "worker.node_identity"
    }
    assert bare == set(), f"模块里出现了裸可变容器：{sorted(bare)}"


def test_ledger_capacity_is_a_constructor_argument() -> None:
    """容量是构造参数：`BootLedger(max_rounds=2)` 直接压边界，不必动生产旋钮 `LEDGER_MAX_ROUNDS`。"""
    led = ni.BootLedger(max_rounds=2)
    assert led.pin("it1", "a98", "g1", 1) is None
    assert led.pin("it2", "a98", "g2", 2) is None
    assert len(led) == 2
    assert led.pin("it3", "a98", "g3", 3) is None
    assert len(led) == 2, "超出容量必须挤掉最旧那轮（内存有界）"
    # 最旧的 it1 已被挤掉 ⇒ 重新钉（None），而不是报「变了」
    assert led.pin("it1", "a98", "g4", 4) is None
    # it3 仍记得：同一轮换 bootId 照样报，且钉住的还是第一代
    why = led.pin("it3", "a98", "other", 99)
    assert why is not None
    assert led.pinned_boot("it3", "a98") == "g3"
    assert led.pinned_boot("it1", "a98") == "g4", "被挤掉的那轮重新钉新值"


def test_ledger_pin_does_not_rewrite_after_a_mismatch() -> None:
    """钉子只钉一次：报过一次不一致之后，再换了第三个 bootId 仍报同一个「第一代 → 新一代」。"""
    led = ni.BootLedger()
    assert led.pin("it1", "a98", "gen1", 1) is None
    first = led.pin("it1", "a98", "gen2", 2)
    second = led.pin("it1", "a98", "gen3", 3)
    assert first is not None and second is not None
    # 钉住的永远是第一代：不一致后不改写（改了 ⇒ 下一轮的「变了」会拿第二代当基准，原因会漂）
    assert led.pinned_boot("it1", "a98") == "gen1"
    # 重复 ping 得到同一行原因（调用方日志/账本幂等）
    assert led.pin("it1", "a98", "gen2", 2) == first

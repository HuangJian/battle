"""test_state_init —— 课程 `state_init`（rollout 起始分布，plan/x20-state-init.plan.md）。

钉四层：**P2**（`CourseConfig` 字段映射 + 启动期自洽检查）、**P3**（派生纯函数 `pick` +
`argv_init_for` 透传 + `build_rollout_cmd` 接线）、**P3.5**（shard 侧 `initTick` 护栏）、
以及**语料身份**（起始分布进 `corpus_identity_fp`）。

为什么 P2 值得一组用例：`CourseConfig` 是 `extra="forbid"`，未映射的课程键 = 课程文件
**加载失败**（`curricula/x20-state-init.jsonc` 就是带着 `state_init` 起草、在 P2 之前加载失败
的）；而映射进去之后，真正的风险变成两个静默错误——
  ① 映射漏了（`ent_break` 前科：课程写了 0.25、训练一直跑 0.6）⇒ 起始分布被静默忽略；
  ② 银行不在盘上却照跑 ⇒ 日志/账本说「中段起跑」，实际跑的是标准开局（换了一个实验）。
所以断言分四类：**值到达 args**、**缺席逐字节不变**、**缺银行/切点自相矛盾在启动期响亮拒**、
**派生同 key 同结果（resume-safe）且换轮换语料**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.dispatch as disp
from rl.config import (
    CourseConfig,
    StateInitBlock,
    apply_course,
    load_course,
)

# 派发脚手架复用：起始分布的闸门住在 `dispatch.run()` 入口（2026-09-25 接线事故修复点），
# 而「ping 门 / 权重下发 / worker 替身」那套确定性脚手架在 rollout 派发用例里——
# `tests/` 是包（有 `__init__.py`），兄弟模块要用裸名 import 得先把本目录塞进 sys.path
# （同 test_hub_auth_d9_order.py 的既有做法）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_rollout_dispatch_resilience import _Harness, _node  # type: ignore

COURSE = "x20-state-init"


def _args(**kw) -> SimpleNamespace:
    """近似 argparse 命名空间（`apply_course` 只需要 mode + 几个 getattr 兜底键）。"""
    d = {"mode": "per-tick", "seed": 7, "out": "tmp/out.json", "traj": "tmp/traj"}
    d.update(kw)
    return SimpleNamespace(**d)


def _bank(dir_path: Path, name: str = "manifest.json") -> Path:
    """盘上的最小银行 manifest（P0 的产物形状：recipe + 逐局 cuts[]）。

    两关各一局、每局两个切点（300/600，都是 K 与 hashInterval 的整数倍）——够测「同 stage
    内按 key 抽局与切点」「跨 stage 不串」「银行没覆盖的关」。
    """
    dir_path.mkdir(parents=True, exist_ok=True)
    def game(stage: int, demo_seed: int, ticks: int) -> dict:
        return {
            "id": f"s{stage}-{demo_seed}",
            "stage": stage,
            "demoSeed": demo_seed,
            "ticks": ticks,
            "cuts": [
                {"tick": 300, "file": f"snapshots/s{stage}-{demo_seed}-t300.json"},
                {"tick": 600, "file": f"snapshots/s{stage}-{demo_seed}-t600.json"},
            ],
        }

    p = dir_path / name
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "recipe": {"cut_from": 300, "cut_to": -120, "cut_step": 300},
                "totals": {"games": 2, "snapshots": 4},
                "games": [game(2000, 414001, 1200), game(2001, 414002, 1500)],
            }
        ),
        encoding="utf-8",
    )
    return p


# ────────────────────────── ① 字段映射：值必须到达 args ──────────────────────────


def test_the_drafted_course_file_loads_and_carries_the_bank_and_cuts() -> None:
    """课程文件（起草稿）能加载，且块内每个键都真的被读进来（不是 extra 忽略）。"""
    c = load_course(COURSE)
    si = c.state_init
    assert si is not None, "curricula/x20-state-init.jsonc 的 state_init 必须被映射"
    assert si.bank == "nn-training/data/state-init-bank/manifest.json"
    assert (si.cut_from, si.cut_to, si.cut_step) == (300, -120, 300)
    assert si.rotate_cuts is True


def test_state_init_reaches_args_as_plain_json_data(tmp_path: Path) -> None:
    """管道：课程键 → `flat_overrides`（整块 → dict）→ args；值必须是 JSON 可序列化的数据。"""
    bank = _bank(tmp_path)
    c = CourseConfig(name="t-si", mode="per-tick", state_init=StateInitBlock(bank=str(bank)))
    o = c.flat_overrides()
    assert "state_init" in o and isinstance(o["state_init"], dict), o
    assert o["state_init"]["bank"] == str(bank)
    assert o["state_init"]["cut_step"] == 300

    args = _args()
    apply_course(args, c)
    assert args.state_init["bank"] == str(bank)
    # echo_config 会对非标量做 json.dumps ⇒ 值必须是 JSON 数据而不是 pydantic 模型
    json.dumps(args.state_init)


# ────────────────────────── ② 缺席 = 现状逐字节不变 ──────────────────────────


def test_absent_state_init_is_byte_identical_to_today() -> None:
    """老课程（没声明 state_init）：不进 overrides、不吃 args 字段、块本身是 None。"""
    plain = CourseConfig(name="old", mode="per-tick")
    assert plain.state_init is None
    assert "state_init" not in plain.flat_overrides()
    args = _args()
    apply_course(args, plain)
    assert not hasattr(args, "state_init"), "缺席时不得凭空给 args 挂一个键"


def test_an_unrelated_curriculum_is_unaffected() -> None:
    """库里其它课程照旧加载（golden 那条 `test_jsonc_courses_load` 的同类断言，盯回归面）。"""
    for name in ("s1", "s4b"):
        c = load_course(name)
        assert c.state_init is None, name
        assert "state_init" not in c.flat_overrides(), name


# ────────────────────────── ③ 启动期响亮拒 ──────────────────────────


def test_missing_bank_file_is_refused_loudly(tmp_path: Path) -> None:
    """银行不在盘上 ⇒ 启动期 SystemExit（**不**静默退回标准开局）。"""
    c = CourseConfig(
        name="t-si",
        mode="per-tick",
        state_init=StateInitBlock(bank=str(tmp_path / "nope" / "manifest.json")),
    )
    with pytest.raises(SystemExit, match="不在盘上"):
        apply_course(_args(), c)


def test_empty_bank_is_refused_loudly() -> None:
    """声明了 state_init 却没给 bank = 空转，同样响亮拒。"""
    with pytest.raises(SystemExit, match="没给 bank"):
        apply_course(_args(), CourseConfig(name="t-si", state_init=StateInitBlock()))


def test_a_bank_written_repo_relative_is_accepted_from_either_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """课程里的数据路径两种基准混用（`nn-training/...` 仓库相对、`data/...` nn-training 相对），
    而训练进程的 cwd 取决于谁拉起来的 ⇒ 两种写法都要认（cwd 与它们都不相同时靠备用基准命中）。"""
    import rl.config as cfg

    fake_nn = tmp_path / "repo" / "nn-training"
    monkeypatch.setattr(cfg, "CURRICULA_DIR", fake_nn / "curricula")
    _bank(fake_nn / "data")
    monkeypatch.chdir(tmp_path)  # cwd 与两种基准都不是同一个地方
    for written in ("nn-training/data/manifest.json", "data/manifest.json"):
        args = _args()
        apply_course(args, CourseConfig(name="t-si", state_init=StateInitBlock(bank=written)))
        assert args.state_init["bank"] == written


@pytest.mark.parametrize(
    "bad, needle",
    [
        ({"cut_from": -1}, "cut_from"),
        ({"cut_to": 120}, "cut_to"),
        ({"cut_step": 0}, "cut_step"),
    ],
)
def test_inconsistent_cuts_are_refused_at_parse_time(bad: dict, needle: str) -> None:
    """切点自相矛盾在校验期就响亮（不用等到开训才发现起始分布是空集/倒序）。"""
    with pytest.raises(ValueError, match=needle):
        StateInitBlock(**bad)


def test_unknown_subkey_is_refused() -> None:
    """块内也不许乱写键（extra=forbid，与 CourseConfig 同规）。

    走 `model_validate` 而不是 `StateInitBlock(bank_of_tapes=…)`：`extra=forbid` 是**运行时**
    校验，mypy 会先把那个不存在的关键字拒掉（用例就没机会跑到 pydantic 那一层）。
    """
    with pytest.raises(ValueError, match="bank_of_tapes"):
        StateInitBlock.model_validate({"bank_of_tapes": "x"})


def test_rebase_counters_is_gone(tmp_path: Path) -> None:
    """`rebase_counters` 已删（M1）：它对奖励是恒等变换（奖励 = Φ 差分），却会让
    `metrics.kills` 与 manifest 的游戏真值分叉。声明它 = 配置写错（extra=forbid），
    而不是静默吃掉——早期起草稿里就有这个键，必须**加载失败**。"""
    with pytest.raises(ValueError, match="rebase_counters"):
        StateInitBlock.model_validate({"bank": str(_bank(tmp_path)), "rebase_counters": True})


# ─────────────── ④ P3：派生纯函数（pick）───────────────


def _index(tmp_path: Path):
    from rl.state_init import load_bank

    return load_bank(_bank(tmp_path))


def test_pick_is_a_pure_function_of_its_key(tmp_path: Path) -> None:
    """同 key 必同值（断点续跑/跨机重放不得换起始状态）+ 切点落在两条对齐线上。"""
    from rl.state_init import pick

    idx = _index(tmp_path)
    a = pick(idx, rotate_seed=99, it=3, stage=2000, seed=7, rotate_cuts=True)
    b = pick(idx, rotate_seed=99, it=3, stage=2000, seed=7, rotate_cuts=True)
    assert a == b and a is not None
    assert a.game.startswith("s2000-") and a.path.endswith(a.snapshot)
    assert a.tick % 10 == 0 and a.tick % 100 == 0, "交棒点必须在决策边界与 hash 采样线上"
    assert a.snapshot in {"s2000-414001-t300.json", "s2000-414001-t600.json"}


def test_pick_rotates_with_it_and_never_crosses_stages(tmp_path: Path) -> None:
    """换 it 必换（§15.1：起始状态也是语料）；stage 2001 的局只可能来自 2001。"""
    from rl.state_init import pick

    idx = _index(tmp_path)
    seen: set[str] = set()
    for it in range(60):
        ref = pick(idx, rotate_seed=99, it=it, stage=2000, seed=7, rotate_cuts=True)
        assert ref is not None
        seen.add(ref.snapshot)
    assert len(seen) > 1, "轮换不成立 = 每轮重磨同一批起始状态（记忆化，指标作废）"
    for it in range(20):
        ref = pick(idx, rotate_seed=99, it=it, stage=2001, seed=7, rotate_cuts=True)
        assert ref is not None and ref.game.startswith("s2001-"), ref


def test_pick_pins_the_first_cut_when_rotation_is_off(tmp_path: Path) -> None:
    """`rotate_cuts=False` = 切点固定、局仍轮换（不消耗随机数）。"""
    from rl.state_init import pick

    idx = _index(tmp_path)
    for it in range(8):
        ref = pick(idx, rotate_seed=99, it=it, stage=2000, seed=7, rotate_cuts=False)
        assert ref is not None and ref.tick == 300, ref


def test_pick_is_none_for_a_stage_the_bank_does_not_cover(tmp_path: Path) -> None:
    """银行没这个关的人类局 ⇒ None（课程层面的事实，不是错误）。"""
    from rl.state_init import pick

    assert pick(_index(tmp_path), rotate_seed=1, it=0, stage=2003, seed=5, rotate_cuts=True) is None


# ─────────── ⑤ P3：argv 透传 + 三条响亮拒 ───────────


def _si_args(bank: Path, **kw) -> SimpleNamespace:
    """近似训练进程的 args（含 `_course_iter` 每轮注入的两个派生 key）。"""
    base = {
        "state_init": {
            "bank": str(bank),
            "cut_from": 300,
            "cut_to": -120,
            "cut_step": 300,
            "rotate_cuts": True,
        },
        "_it": 2,
        "_rotate_seed": 1789876303,
    }
    base.update(kw)
    return _args(**base)


def test_argv_init_for_derives_a_snapshot_path(tmp_path: Path) -> None:
    from rl.state_init import argv_init_for

    ref = argv_init_for(_si_args(_bank(tmp_path)), stage=2000, seed=7)
    assert ref is not None and ref.path.endswith(".json") and ref.tick % 10 == 0


def test_argv_init_for_is_a_noop_without_the_course_block() -> None:
    """没开 state_init 的课程 ⇒ None（零回归）；即使 args 上什么都没有也不炸。"""
    from rl.state_init import argv_init_for

    assert argv_init_for(_args(), stage=2000, seed=7) is None


def test_argv_init_for_refuses_without_the_rotation_key_halves(tmp_path: Path) -> None:
    """缺 `_it`/`_rotate_seed` = 采集入口没走 `_course_iter` ⇒ 拒发，不猜另一个轮次。"""
    from rl.state_init import argv_init_for

    for missing in ("_it", "_rotate_seed"):
        args = _si_args(_bank(tmp_path))
        delattr(args, missing)
        with pytest.raises(SystemExit, match="派生需要"):
            argv_init_for(args, stage=2000, seed=7)


def test_argv_init_for_refuses_cloud_rounds(tmp_path: Path) -> None:
    """云侧快照搬运未落地（plan §P2.5）：上云轮的 argv 里给一个仓库相对路径，节点上没有那个
    文件，而老导出器**静默忽略未知 flag** ⇒ 云上跑标准开局、账本写中段起跑。宁可不发。"""
    from rl.state_init import argv_init_for

    with pytest.raises(SystemExit, match="云侧"):
        argv_init_for(_si_args(_bank(tmp_path)), stage=2000, seed=7, node_side=True)


def test_argv_init_for_refuses_a_stage_without_bank_games(tmp_path: Path) -> None:
    """银行不覆盖这个关 ⇒ 响亮拒（不静默退回标准开局：那是另一个实验）。"""
    from rl.state_init import argv_init_for

    with pytest.raises(SystemExit, match="没有 stage"):
        argv_init_for(_si_args(_bank(tmp_path)), stage=2003, seed=7)


def test_build_rollout_cmd_appends_the_snapshot_flag(tmp_path: Path) -> None:
    """命令拼装唯一点：开了 state_init 才追加 `--init-snapshot`（其余 flag 不受影响）。"""
    from rl.cmd import build_rollout_cmd

    bank = _bank(tmp_path)
    with_flag = build_rollout_cmd(
        "bun",
        _si_args(bank, max_ticks=12900, difficulty="hard", dodge=""),
        weights="tmp/w.json",
        out_dir="tmp/o",
        stage=2000,
        seed=7,
        wver="v",
        node_label="local",
    )
    assert "--init-snapshot" in with_flag
    snap_arg = with_flag[with_flag.index("--init-snapshot") + 1]
    assert snap_arg.endswith("t300.json") or snap_arg.endswith("t600.json"), snap_arg
    plain = build_rollout_cmd(
        "bun",
        _args(max_ticks=12900, difficulty="hard", dodge=""),
        weights="tmp/w.json",
        out_dir="tmp/o",
        stage=2000,
        seed=7,
        wver="v",
        node_label="local",
    )
    assert "--init-snapshot" not in plain, "没开 state_init 时逐字节旧行为"


def test_build_rollout_cmd_refuses_node_side_runs(tmp_path: Path) -> None:
    """同一门课由**节点**执行（逐轮上云 / 半离线整段）⇒ 发布前就拒，不留半份 job。"""
    from rl.cmd import build_rollout_cmd

    with pytest.raises(SystemExit, match="云侧"):
        build_rollout_cmd(
            "bun",
            _si_args(_bank(tmp_path), max_ticks=12900, difficulty="hard", dodge=""),
            weights="tmp/w.json",
            out_dir="tmp/o",
            stage=2000,
            seed=7,
            wver="v",
            node_label="node",
            node_side=True,
        )


# ─────── ⑥ P3.5：shard 侧护栏（initTick）+ 起始分布进语料身份 ───────

WVER = "wv-si"


def _shard(root: Path, stage: int, seed: int, *, init_tick: int | None) -> Path:
    """最小完整 shard：`init_tick=None` 模拟标准开局的产物（老节点忽略 flag / 派发漏接）。"""
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    mm: dict = {"wver": WVER, "stage": stage, "seed": seed, "nSamples": 30}
    if init_tick is not None:
        mm["initTick"] = init_tick
    (d / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")
    return d


def test_scan_shards_drops_shards_that_did_not_start_from_a_snapshot(tmp_path: Path) -> None:
    """开了 state_init ⇒ 缺 `initTick` 的 shard 不算 done（否则整批中段局被静默跳过）。

    默认 `state_init=False` 必须逐字节旧行为——同一个目录、同一份 manifest。
    """
    from rl.resume import completed_pairs, settled_stage_totals

    _shard(tmp_path, 2000, 11, init_tick=300)
    _shard(tmp_path, 2000, 12, init_tick=None)
    assert completed_pairs(tmp_path, WVER) == {(2000, 11), (2000, 12)}
    assert completed_pairs(tmp_path, WVER, state_init=True) == {(2000, 11)}
    assert settled_stage_totals(tmp_path, WVER, state_init=True) == {2000: (1, 30)}


def test_publish_set_excludes_shards_that_did_not_start_from_a_snapshot(tmp_path: Path) -> None:
    """hub 打包端同一条规则：标准开局的 shard 不进 payload（data_fp 照样匹配 = 静默换实验）。"""
    from remote.hub_client import iter_shard_dirs

    ok = _shard(tmp_path / "it1", 2000, 11, init_tick=300)
    bad = _shard(tmp_path / "it1", 2000, 12, init_tick=None)
    for d in (ok, bad):
        (d / "obs.npy").write_bytes(b"")
    assert iter_shard_dirs(tmp_path, 1) == [ok, bad]
    assert iter_shard_dirs(tmp_path, 1, state_init=True) == [ok]


def test_corpus_identity_carries_the_start_distribution(tmp_path: Path) -> None:
    """起始分布决定「一个样本从哪个世界开始」⇒ 进语料身份（D14）：课程中途加/删/改
    state_init 会让旧 shard 在**所有** funnel 被自动排除，零额外参数。

    路径写法（仓库相对 vs 绝对）**不得**改变身份——否则 hub（cwd=仓库）与节点（cwd=job 目录）
    算出的指纹不同，整份 job 被拒。
    """
    from rl.config import corpus_identity_fp

    bank = _bank(tmp_path / "bank")
    plain = CourseConfig(name="t", mode="per-tick", level="ladder-c20-lives1")
    si = CourseConfig(
        name="t", mode="per-tick", level="ladder-c20-lives1", state_init=StateInitBlock(bank=str(bank))
    )
    spelled = CourseConfig(
        name="t",
        mode="per-tick",
        level="ladder-c20-lives1",
        state_init=StateInitBlock(bank=str(tmp_path / "elsewhere" / bank.name)),
    )
    other_cut = CourseConfig(
        name="t",
        mode="per-tick",
        level="ladder-c20-lives1",
        state_init=StateInitBlock(bank=str(bank), cut_step=600),
    )
    no_rotate = CourseConfig(
        name="t",
        mode="per-tick",
        level="ladder-c20-lives1",
        state_init=StateInitBlock(bank=str(bank), rotate_cuts=False),
    )

    assert corpus_identity_fp(plain) != corpus_identity_fp(si)
    assert corpus_identity_fp(si) == corpus_identity_fp(spelled)
    assert corpus_identity_fp(si) != corpus_identity_fp(other_cut)
    assert corpus_identity_fp(si) != corpus_identity_fp(no_rotate)


def test_state_init_enabled_reads_args_only() -> None:
    """护栏开关的唯一来源：`args.state_init`（缺席 = False，老路径零开销）。"""
    from rl.resume import state_init_enabled

    assert state_init_enabled(_args()) is False
    assert state_init_enabled(_args(state_init={"bank": "x"})) is True
    assert state_init_enabled(SimpleNamespace()) is False


# ───────── ⑥ 派发闸门（2026-09-25 接线事故修复，plan §P3 补丁）─────────
# 事故：`--init-snapshot` 当时只接进了 `build_rollout_cmd`，而**主循环走的是 `dispatch.run()`
# 的 volume 路**（`fetch_task` 拼任务参数时没有快照项）——`local_slots` 只是并发配额，没有
# 「纯本机」开关，主循环永远向 pool 派发。于是云上跑标准开局、账本记中段起跑，缺 `initTick`
# 的 shard 又被护栏在六个 funnel 全剔（波次永远凑不齐）⇒ 混语料 + 无限波次，烧掉 51.9 万
# transitions 才发现。修法 = 闸门住在 `dispatch.run()` 入口：开了 `state_init` 的轮整轮纯本机。


def test_state_init_refuses_to_dispatch_to_the_pool(tmp_path, monkeypatch) -> None:
    """开 state_init + 有可行远端节点 ⇒ 派发前 SystemExit（一局也不进 pool）。

    v1 的快照只在本机盘上（plan §P2.5 未落地）：节点腿拿到的是一个仓库相对路径、文件不在
    那儿，而导出器静默忽略未知 flag ⇒ 唯一安全的结局是拒发。
    """
    h = _Harness(tmp_path, monkeypatch, games=2)  # 缺省 nodes=[a97] 且 ping 绿
    h.args.state_init = {"bank": str(tmp_path / "bank" / "manifest.json")}

    def fetch(*_a, **_kw):
        raise AssertionError("state_init 轮不得向 pool 派发（节点腿拼 argv 没有快照项）")

    with pytest.raises(SystemExit, match="快照只在本机盘上"):
        h.run(fetch)


def test_state_init_without_remote_nodes_runs_local_only(tmp_path, monkeypatch) -> None:
    """开 state_init 且没有可行远端节点 ⇒ 整轮交本机腿（`run_rollout`），零派发。

    「零派发」由 `fetch_task` 替身断言：只要有人取活就炸。
    """
    h = _Harness(
        tmp_path,
        monkeypatch,
        games=3,
        nodes=[_node("a97")],
        ping_fn=lambda *_a, **_k: None,  # 本轮远端节点全部不可用
    )
    h.args.state_init = {"bank": str(tmp_path / "bank" / "manifest.json")}
    seen: dict = {}

    def fake_local(bun, rl_path, traj_dir, pairs, args):
        seen["pairs"] = list(pairs)
        return {"games": len(pairs)}

    monkeypatch.setattr(disp, "run_rollout", fake_local)

    def fetch(*_a, **_kw):
        raise AssertionError("零派发")

    report = h.run(fetch)

    assert seen["pairs"] == [(2000, 1), (2000, 2), (2000, 3)], seen
    assert "state_init: local-only round" in "\n".join(h.logs)
    assert report == {"games": 3}


def test_state_init_off_keeps_dispatching_to_the_pool(tmp_path, monkeypatch) -> None:
    """缺席（老课程）⇒ 逐字节旧行为：照旧向节点派发，`fetch_task` 拿到每一局。"""
    h = _Harness(tmp_path, monkeypatch, games=2)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        return h.manifest(), {}

    report = h.run(fetch)

    assert calls["n"] == 2
    assert report["dist"]["nodes"] == {"a97": 2}, report["dist"]


def test_every_local_argv_carries_a_snapshot(tmp_path, monkeypatch) -> None:
    """闸门放行的那条腿（纯本机）逐局 argv 都带 `--init-snapshot`——**一次性 spawn 那条路**。

    32 局里混进一局标准开局就是一局混语料（那局的 shard 还会被 `initTick` 护栏剔除）；
    所以钉在 `run_rollout` 这一层而不是只钉 `build_rollout_cmd`。
    `NN_SERVE_POOL=0` 强制走回退路径（池化那条路由下一例钉）。
    """
    import subprocess as sp

    import rl.queue_local as ql

    monkeypatch.setenv("NN_SERVE_POOL", "0")  # 本条钉一次性路径（池化版见下一例）
    bank = _bank(tmp_path / "bank")
    weights = tmp_path / "w.json"
    weights.write_text('{"arch":{}}', encoding="utf-8")
    traj = tmp_path / "it1"
    traj.mkdir()
    args = _si_args(bank, max_ticks=12900, difficulty="hard", dodge="", workers=3)
    seen: list[list[str]] = []

    class _Proc:
        returncode = 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd, **_kw):
        seen.append(list(cmd))
        out = Path(cmd[cmd.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "_rl_report.json").write_text(json.dumps({"games": 1}), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(sp, "Popen", fake_popen)
    ql.run_rollout("bun", str(weights), traj, [(2000, 1), (2000, 2), (2000, 3)], args)

    assert len(seen) == 3, seen
    allowed = {"s2000-414001-t300.json", "s2000-414001-t600.json"}  # 银行里 stage 2000 的两个切点
    for cmd in seen:
        assert "--init-snapshot" in cmd, cmd
        snap = Path(cmd[cmd.index("--init-snapshot") + 1]).name
        assert snap in allowed, snap
        assert "--stage-json" not in cmd  # 顺手钉住：注入不影响其余 argv


def test_the_local_pool_serves_every_game_with_the_snapshot(tmp_path, monkeypatch) -> None:
    """本机腿的**长驻池**（`--serve`）逐局 argv 都带 `--init-snapshot`，且一局都不 spawn。

    为什么值得单独钉（2026-09-25）：本机腿原先每局 `Popen` 一个 bun，池化后 argv 改走 stdin
    （`cmd[1:]`，不含 bun 本身）——送错一段（带上 bun / 带上脚本路径）或忘了注入快照，都会表现为
    「跑起来了、但起始分布不是你要的那个」，而日志里只有一行「已结算」。
    """
    import rl.queue_local as ql
    from remote import serve_pool

    bank = _bank(tmp_path / "bank")
    weights = tmp_path / "w.json"
    weights.write_text('{"arch":{}}', encoding="utf-8")
    traj = tmp_path / "it1"
    traj.mkdir()
    args = _si_args(bank, max_ticks=12900, difficulty="hard", dodge="", workers=3)
    served: list[list[str]] = []
    scripts: list[str] = []
    pools: list = []

    class _FakePool:
        def __init__(self, script: str) -> None:
            self.script = script
            self.closed = False

        def start(self) -> int:
            return 3

        def try_pool(
            self, argv, log_path, timeout_sec, *, label="?", attempt=1, kind="rollout"
        ) -> float:
            served.append(list(argv))
            out = Path(argv[argv.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "_rl_report.json").write_text(json.dumps({"games": 1}), encoding="utf-8")
            log_path.write_text("served\n", encoding="utf-8")
            return 0.01

        def summary(self) -> str:
            return f"serve_pool: served={len(served)} died=0"

        def close(self) -> None:
            self.closed = True

    def fake_make_pool(bun, script, ts_dir, workers, log_fn):
        scripts.append(str(script))
        p = _FakePool(str(script))
        pools.append(p)
        return p

    monkeypatch.setattr(serve_pool, "make_pool", fake_make_pool)

    def boom(*_a, **_k):
        raise AssertionError("池已服务了每一局 —— 不该还有人 spawn 一次性进程")

    monkeypatch.setattr(ql.subprocess, "Popen", boom)
    logs: list[str] = []
    monkeypatch.setattr(ql, "log", logs.append)
    report = ql.run_rollout("bun", str(weights), traj, [(2000, 1), (2000, 2), (2000, 3)], args)

    assert scripts == ["tools/sim/export-rl-rollout.ts"], scripts  # 按真 argv 选脚本
    assert len(served) == 3, served
    allowed = {"s2000-414001-t300.json", "s2000-414001-t600.json"}
    for argv in served:
        assert argv[0] == "tools/sim/export-rl-rollout.ts", argv  # 不含 bun 本身
        assert "--init-snapshot" in argv, argv
        assert Path(argv[argv.index("--init-snapshot") + 1]).name in allowed, argv
    assert report["games"] == 3, report
    assert pools and pools[0].closed, "池必须由 run_rollout 收掉（否则每轮留一批常驻 bun）"
    # 收益/回退要看得见（轮末一行汇总）——巡检靠它判断池到底生效没有
    assert any("serve_pool: served=3" in m for m in logs), logs

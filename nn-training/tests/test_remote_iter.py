"""test_remote_iter.py — M3（rollout 上云，kind=iter）协议 + 规格 + 节点侧执行器单测。

plan/remote-wire-remediation.plan.md §5.2/§5.5。覆盖：
  * manifest：kind=iter 的必填（ts_code_sha256/rollout）、mode 互斥红线、rollout 规格
    逐字段校验（argv 白名单 / job 目录内相对路径 / 逐局 stage,seed 不重复）；
  * data_fp：声明集算的期望值与「实产目录算的 data_fp」同函数同结果（节点侧复算的
    正确性靠这条守着——两侧漂了就等于「实产集 == 声明集」不再成立）；
  * result：iter 结果必须带采集报告（冒烟回显豁免）；
  * v2 job 体：ts_code 段往返 + 旧体（无 has_ts）仍能解（wire 兼容红线）；
  * hub 侧 build_iter_spec：命令来自 `rl/cmd.build_rollout_cmd`（单源），路径一律
    job 目录内相对路径，bun 路径被剥掉（节点用自己的）；
  * 节点侧 run_iter_rollout：用**假 bun**（python 桩）跑全流程——产 shard、聚合报告、
    实产集与声明集不符时响亮拒绝。

真 bun + 真 exporter 的逐位对拍在 `e2e/test_iter_real_bun.py`（本文件不依赖 bun）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.iter_rollout as iter_rollout
from remote.iter_rollout import run_iter_rollout, scan_shard_dirs, verify_shards
from remote.protocol import (
    INIT_WEIGHTS_NAME,
    ROLLOUT_SCRIPTS,
    TS_CODE_NAME,
    WIRE_JOB_MAGIC,
    ProtocolError,
    RetryableError,
    data_fp,
    iter_declared_entries,
    iter_expected_data_fp,
    normalize_manifest,
    pack_job_v2,
    shard_name,
    unpack_job_v2,
    validate_result,
    validate_rollout_spec,
)
from rl.iter_job import build_iter_spec
from rl.loop_steps import TrainingSteps

# ------------------------------------------------------------------ fixtures


def _mini_manifest(**over) -> dict:
    """最小合法 **iter** manifest（必填齐全；over 覆盖任意字段）。"""
    m = {
        "proto": 1,
        "runId": "test-run",
        "it": 3,
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "ts_code_sha256": "t" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 2,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
        "kind": "iter",
        "rollout": _spec(games=[(0, 0)]),
    }
    m.update(over)
    return m


def _argv(stage: int, seed: int, out: str, wver: str = "W" * 64) -> list[str]:
    return [
        "tools/sim/export-rl-rollout.ts",
        "--weights",
        INIT_WEIGHTS_NAME,
        "--out",
        out,
        "--stages",
        str(stage),
        "--seeds",
        str(seed),
        "--max-ticks",
        "12000",
        "--difficulty",
        "hard",
        "--wver",
        wver,
        "--node-label",
        "node",
    ]


def _spec(games: list[tuple[int, int]], **over) -> dict:
    s = {
        "argv": [_argv(st, sd, f"w{i}") for i, (st, sd) in enumerate(games)],
        "wver": "W" * 64,
        "workers": 2,
        "game_timeout_sec": 0.0,
        "bun": "bun",
    }
    s.update(over)
    return s


def _write_shard(d: Path, stage: int, seed: int, wver: str = "W" * 64) -> None:
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "obs.npy", np.zeros((2, 14, 26, 26), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps({"stage": stage, "seed": seed, "wver": wver, "nSamples": 2}),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ manifest


def test_iter_manifest_normalizes_and_keeps_spec() -> None:
    m = normalize_manifest(_mini_manifest())
    assert m["kind"] == "iter"
    assert m["ts_code_sha256"] == "t" * 64
    assert m["rollout"]["workers"] == 2
    assert m["rollout"]["argv"][0][0] == "tools/sim/export-rl-rollout.ts"


@pytest.mark.parametrize("drop", ["ts_code_sha256", "rollout"])
def test_iter_manifest_requires_new_fields(drop: str) -> None:
    m = _mini_manifest()
    m.pop(drop)
    with pytest.raises(ProtocolError) as e:
        normalize_manifest(m)
    assert drop in str(e.value)


def test_iter_manifest_requires_per_tick_mode() -> None:
    with pytest.raises(ProtocolError) as e:
        normalize_manifest(_mini_manifest(mode="bc"))
    assert "per-tick" in str(e.value)


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ProtocolError) as e:
        normalize_manifest(_mini_manifest(kind="bogus"))
    assert "未知" in str(e.value)


def test_ppo_manifest_untouched_by_iter_support() -> None:
    """非 iter 的 manifest 不得被迫带 ts_code/rollout（wire 兼容的最基本要求）。"""
    m = _mini_manifest(kind="ppo")
    m.pop("rollout")
    m.pop("ts_code_sha256")
    out = normalize_manifest(m)
    assert out["kind"] == "ppo"
    assert "rollout" not in out or out.get("rollout") is None


# ------------------------------------------------------------------ rollout 规格


def test_spec_rejects_unknown_keys_not_ignores() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_rollout_spec({**_spec([(0, 0)]), "surprise": 1})
    assert "未知字段" in str(e.value)


def test_spec_argv_whitelist_rejects_arbitrary_executable() -> None:
    bad = _spec([(0, 0)])
    bad["argv"][0][0] = "sh"
    with pytest.raises(ProtocolError) as e:
        validate_rollout_spec(bad)
    assert "白名单" in str(e.value)
    assert ROLLOUT_SCRIPTS == ("tools/sim/export-rl-rollout.ts",)


@pytest.mark.parametrize("evil", ["../escape", "/abs/path", "~/x", ""])
def test_spec_argv_paths_must_stay_inside_job_dir(evil: str) -> None:
    bad = _spec([(0, 0)])
    i = bad["argv"][0].index("--out") + 1
    bad["argv"][0][i] = evil
    with pytest.raises(ProtocolError):
        validate_rollout_spec(bad)


@pytest.mark.parametrize(
    "evil",
    [
        "C:/weights.json",  # Windows 盘符绝对（POSIX 下 os.path.isabs=None）
        "C:\\weights.json",  # 反斜杠写法
        "c:weights.json",  # drive-relative：同样不在 job 目录内
        "\\\\host\\share\\weights.json",  # UNC 共享
        "//host/share/weights.json",
    ],
)
def test_spec_argv_weights_must_be_relative(evil: str) -> None:
    """盘符/UNC 路径必须在**任何平台**都被拒（节点的 cwd 契约不随 hub 内核变）。"""
    bad = _spec([(0, 0)])
    i = bad["argv"][0].index("--weights") + 1
    bad["argv"][0][i] = evil
    with pytest.raises(ProtocolError):
        validate_rollout_spec(bad)


def test_spec_argv_plain_relative_paths_still_pass() -> None:
    """反向断言：收紧盘符判定不得误伤正常相对路径（含带点的目录名）。"""
    for ok in ("weights.json", "sub/dir/w.json", "./w.json", "w-1.2.json", "a_b/c.json"):
        good = _spec([(0, 0)])
        i = good["argv"][0].index("--weights") + 1
        good["argv"][0][i] = ok
        assert validate_rollout_spec(good)["argv"][0][i] == ok


def test_spec_rejects_duplicate_game() -> None:
    bad = _spec([(0, 0), (0, 0)])
    with pytest.raises(ProtocolError) as e:
        validate_rollout_spec(bad)
    assert "重复" in str(e.value)


def test_spec_rejects_duplicated_stage_flag() -> None:
    """`--stages 0 --stages 1` 会被导出器只吃一个，而声明集可能按另一个算 ⇒ 拒收。"""
    bad = _spec([(0, 0)])
    bad["argv"][0] += ["--stages", "1"]
    with pytest.raises(ProtocolError) as e:
        validate_rollout_spec(bad)
    assert "恰好出现 1 次" in str(e.value)


def test_spec_rejects_missing_out_or_weights() -> None:
    for flag in ("--out", "--weights"):
        bad = _spec([(0, 0)])
        a = bad["argv"][0]
        i = a.index(flag)
        bad["argv"][0] = a[:i] + a[i + 2 :]
        with pytest.raises(ProtocolError):
            validate_rollout_spec(bad)


def test_spec_workers_and_timeout_validated() -> None:
    with pytest.raises(ProtocolError):
        validate_rollout_spec(_spec([(0, 0)], workers=0))
    with pytest.raises(ProtocolError):
        validate_rollout_spec(_spec([(0, 0)], game_timeout_sec=-1))


# ------------------------------------------------------------------ data_fp


def test_expected_data_fp_equals_actual_dirs_fp(tmp_path: Path) -> None:
    """核心等价性：声明集算的期望值 == 实产目录算的 data_fp（两侧同函数）。

    这条是节点侧 `verify_shards` 的立论基础——不是「像」，是同一个函数的两次调用。
    """
    spec = validate_rollout_spec(_spec([(3, 7), (3, 8)]))
    for st, sd in ((3, 7), (3, 8)):
        _write_shard(tmp_path / shard_name(st, sd), st, sd)
    dirs = scan_shard_dirs(tmp_path)
    assert data_fp(dirs) == iter_expected_data_fp(spec)


def test_declared_entries_follow_argv_order_independent_of_input() -> None:
    spec = validate_rollout_spec(_spec([(9, 1), (3, 2)]))
    ents = iter_declared_entries(spec)
    assert [e[0] for e in ents] == [
        shard_name(9, 1),
        shard_name(3, 2),
    ]
    assert all(e[1] == "W" * 64 for e in ents)


def test_verify_shards_rejects_extra_and_missing(tmp_path: Path) -> None:
    import shutil

    spec = validate_rollout_spec(_spec([(3, 7), (3, 8)]))
    exp = iter_expected_data_fp(spec)
    _write_shard(tmp_path / shard_name(3, 7), 3, 7)  # 少一局
    with pytest.raises(ProtocolError) as e:
        verify_shards(tmp_path, exp)
    assert "声明不符" in str(e.value)
    # 换一个 seed（声明里没有）
    shutil.rmtree(tmp_path / shard_name(3, 7))
    _write_shard(tmp_path / shard_name(3, 9), 3, 9)
    with pytest.raises(ProtocolError):
        verify_shards(tmp_path, exp)
    # 补齐声明集 → 通过
    shutil.rmtree(tmp_path / shard_name(3, 9))
    _write_shard(tmp_path / shard_name(3, 7), 3, 7)
    _write_shard(tmp_path / shard_name(3, 8), 3, 8)
    assert len(verify_shards(tmp_path, exp)) == 2
    # 多一局也拒（「多采了」与「少采了」同样算错——多出来的那局不在任何人的账上）
    _write_shard(tmp_path / shard_name(3, 99), 3, 99)
    with pytest.raises(ProtocolError):
        verify_shards(tmp_path, exp)


def test_verify_shards_rejects_wver_drift(tmp_path: Path) -> None:
    """同 (stage,seed) 但 wver 不同 = 用别的权重采的语料 —— data_fp 必须露出来。"""
    spec = validate_rollout_spec(_spec([(3, 7)]))
    _write_shard(tmp_path / shard_name(3, 7), 3, 7, wver="X" * 64)
    with pytest.raises(ProtocolError):
        verify_shards(tmp_path, iter_expected_data_fp(spec))


def test_verify_shards_empty_dir_is_loud(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError) as e:
        verify_shards(tmp_path, "d" * 64)
    assert "没有任何 shard" in str(e.value)


def test_scan_finds_nested_shards_but_ignores_incomplete(tmp_path: Path) -> None:
    """两种布局都要认：本模块产的在 `w{i}/` 下，payload 解包出来的平铺在 job 根。

    名字合法但没有 manifest.json 的半成品目录不算产出（与本仓 iter_shard_dirs 同口径）。
    """
    _write_shard(tmp_path / "w0" / shard_name(1, 1), 1, 1)  # 节点侧布局（嵌套）
    _write_shard(tmp_path / shard_name(2, 2), 2, 2)  # 解包布局（平铺）
    (tmp_path / "rl_s1_seed2").mkdir()  # 名字合法但没有 manifest
    (tmp_path / "w0" / "rl_sX_seedY").mkdir()  # 名字非法
    assert sorted(p.name for p in scan_shard_dirs(tmp_path)) == [
        shard_name(1, 1),
        shard_name(2, 2),
    ]


# ------------------------------------------------------------------ result


def _iter_result(**over) -> dict:
    r = {
        "job_id": "j" * 16,
        "data_fp": "d" * 64,
        "init_weights_fp": "w" * 64,
        "weights_json": "AAAA",
        "commit_echo": "c" * 40,
        "report": {
            "games": 2,
            "winRate": 0.5,
            "outcomes": {"stage_clear": 1, "timeout": 1},
            "totalSamples": 100,
            "totalTicks": 900,
            "elapsedSec": 12.5,
            "shards": 2,
        },
        "agg": {
            "policy": 0.1,
            "value": 0.2,
            "entropy": 0.3,
            "kl": 0.001,
            "mean_ret": 0.5,
        },
    }
    r.update(over)
    return r


def test_iter_result_requires_report() -> None:
    m = normalize_manifest(_mini_manifest())
    with pytest.raises(ProtocolError) as e:
        validate_result(_iter_result(report=None), m, commit_echo_must_match=False)
    assert "report" in str(e.value)
    out = validate_result(_iter_result(), m, commit_echo_must_match=False)
    assert out["report"]["winRate"] == 0.5


@pytest.mark.parametrize(
    "patch",
    [
        {"games": 0},
        {"outcomes": {}},
        {"totalSamples": -1},
        {"winRate": "x"},
        {"elapsedSec": "x"},
    ],
)
def test_iter_report_field_types_checked(patch: dict) -> None:
    m = normalize_manifest(_mini_manifest())
    r = _iter_result()
    r["report"].update(patch)
    with pytest.raises(ProtocolError):
        validate_result(r, m, commit_echo_must_match=False)


def test_iter_report_missing_key_named_in_error() -> None:
    m = normalize_manifest(_mini_manifest())
    r = _iter_result()
    r["report"].pop("shards")
    with pytest.raises(ProtocolError) as e:
        validate_result(r, m, commit_echo_must_match=False)
    assert "shards" in str(e.value)


def test_smoke_result_exempt_from_report() -> None:
    """冒烟回显刻意不跑 rollout：不该被报告校验挡住（否则 --smoke 直接不可用）。"""
    m = normalize_manifest(_mini_manifest())
    # 冒烟回显仍然有 agg（worker 的 echo 分支填零值），只是没有 report。
    r = _iter_result(report=None, smoke=True)
    out = validate_result(r, m, commit_echo_must_match=False)
    assert out["job_id"] == m["job_id"]
    assert out.get("report") is None  # 未被强行填一份空报告冒充采集口径


# ------------------------------------------------------------------ v2 job 体


def test_job_v2_carries_ts_code_and_keeps_backward_compat() -> None:
    m = _mini_manifest()
    payload, code, ts = b"payload-bytes", b"code-bytes", b"ts-code-bytes"
    body = pack_job_v2(m, payload, code, {"opt": b"o"}, ts)
    assert body.startswith(WIRE_JOB_MAGIC)
    out = unpack_job_v2(body)
    assert b"ts-code-bytes" in body  # 裸二进制段（不是 base64）
    assert out["manifest"] == m
    for field, raw in (("payload_b64", payload), ("code_b64", code), ("ts_code_b64", ts)):
        import base64 as _b64

        assert _b64.b64decode(out[field]) == raw
    assert out["blobs"] == {"opt": "bw=="}
    # 旧体（无 ts_code 段）仍要能解——additive 兼容。
    import base64 as _b64

    old = json.dumps(
        {
            "manifest": m,
            "has_code": True,
            "blob_names": [],
            "lens": [len(payload), len(code)],
        },
        separators=(",", ":"),
    ).encode()
    import struct as _struct

    old_body = WIRE_JOB_MAGIC + _struct.pack(">I", len(old)) + old + payload + code
    o = unpack_job_v2(old_body)
    assert "ts_code_b64" not in o
    assert _b64.b64decode(o["payload_b64"]) == payload


def test_job_v2_ts_code_included_in_sha_check() -> None:
    """ts_code 必须真进体（否则 hub 传了字节、节点收到的却是 None）。"""
    body = pack_job_v2(_mini_manifest(), b"p", None, None, b"TS" * 100)
    assert b"TS" * 100 in body


# ------------------------------------------------------------------ hub 侧规格


def _rollout_args(**over) -> SimpleNamespace:
    a = SimpleNamespace(
        goal_rollout=False,
        intent_rollout=False,
        max_ticks=12000,
        difficulty="hard",
        dodge="",
        course_obj=None,
        course_path="",
        course_frozen_bytes=None,
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_build_iter_spec_paths_are_job_relative() -> None:
    spec = build_iter_spec(_rollout_args(), [(3, 7), (3, 8)], wver="W" * 64, workers=4)
    norm = validate_rollout_spec(spec)
    assert norm["workers"] == 4
    assert norm["wver"] == "W" * 64
    for i, argv in enumerate(norm["argv"]):
        assert argv[0] == "tools/sim/export-rl-rollout.ts"
        assert argv[argv.index("--out") + 1] == f"w{i}"
        assert argv[argv.index("--weights") + 1] == INIT_WEIGHTS_NAME
        assert not any(p.startswith(("/", "C:", "\\")) for p in argv)
        assert argv[argv.index("--node-label") + 1] == "node"


def test_build_iter_spec_never_leaks_hub_bun_path() -> None:
    spec = build_iter_spec(
        _rollout_args(), [(0, 0)], wver="W" * 64, hub_bun=r"C:\Users\x\.bun\bin\bun.exe"
    )
    assert spec["bun"] == "bun"
    assert all(not any(x.endswith(".exe") for x in argv) for argv in spec["argv"])


def test_build_iter_spec_workers_default_is_one() -> None:
    assert build_iter_spec(_rollout_args(), [(0, 0)], wver="w", workers=0)["workers"] == 1


def test_build_iter_spec_empty_pairs_is_refused() -> None:
    with pytest.raises(ValueError):
        build_iter_spec(_rollout_args(), [], wver="w")


def test_build_iter_spec_dodge_and_custom_stage_flow_through() -> None:
    """课程覆盖必须原样出现在节点侧命令里（否则节点采的语料与声明不符）。"""
    spec = validate_rollout_spec(
        build_iter_spec(_rollout_args(dodge="l0", lives_override=1), [(9, 9)], wver="w")
    )
    argv = spec["argv"][0]
    assert argv[argv.index("--dodge") + 1] == "l0"
    assert argv[argv.index("--lives-override") + 1] == "1"


# ------------------------------------------------------------------ 发布 / 落位（hub 侧整链）


def _publish_iter(tmp_path: Path, *, games=((3, 7),)) -> tuple[dict, Path]:
    from remote.hub_client import publish_job

    w = tmp_path / "init_weights.json"
    w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    jsonl = tmp_path / "run.jsonl"
    jsonl.write_text("", encoding="utf-8")
    ts = tmp_path / TS_CODE_NAME
    ts.write_bytes(b"PK\x03\x04" + b"ts-bytes" * 10)
    spec = validate_rollout_spec(_spec(list(games)))
    m = publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=jsonl,
        run_id="r",
        it=1,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='{"reward":{"formula":"score"}}',
        course_fp="f" * 64,
        corpus_fp="",
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        init_weights_path=str(w),
        kind="iter",
        rollout_spec=spec,
        ts_code_sha256="t" * 64,
        ts_code_zip_path=ts,
        log=lambda _m: None,
    )
    return m, w


def test_publish_iter_manifest_and_layout(tmp_path: Path) -> None:
    """发布 iter job：无 shard、data_fp 是声明集、ts_code 拷进 job 目录、init 权重在 payload。"""
    import tarfile as _tarfile

    from remote.protocol import find_payload


    spec = validate_rollout_spec(_spec([(3, 7), (3, 8)]))
    m, w = _publish_iter(tmp_path, games=((3, 7), (3, 8)))
    assert m["kind"] == "iter"
    assert m["data_fp"] == iter_expected_data_fp(spec)
    assert m["ts_code_sha256"] == "t" * 64
    assert m["rollout"]["argv"][0][0] == "tools/sim/export-rl-rollout.ts"
    jd = tmp_path / "jobs" / m["job_id"]
    assert (jd / TS_CODE_NAME).read_bytes() == (tmp_path / TS_CODE_NAME).read_bytes()
    pl = find_payload(jd)
    assert pl is not None
    with _tarfile.open(pl) as tf:
        names = tf.getnames()
    # 节点要用 init 权重跑 rollout ⇒ 即使开了瘦身也必须在 payload 里（M2 B4 的例外）
    assert INIT_WEIGHTS_NAME in names
    assert not any(n.startswith("rl_s") for n in names), "iter payload 不得含 shard"
    assert m["payload_sha256"]


def test_publish_iter_refuses_local_shards(tmp_path: Path) -> None:
    """混着发（既有本地 shard 又声明上云）= 双份采集 —— 发布端就拒。"""
    from remote.hub_client import HubClientError, publish_job

    spec = validate_rollout_spec(_spec([(3, 7)]))
    sd = tmp_path / shard_name(3, 7)
    _write_shard(sd, 3, 7)
    w = tmp_path / "w.json"
    w.write_text("{}", encoding="utf-8")
    with pytest.raises(HubClientError) as e:
        publish_job(
            job_root=tmp_path / "jobs",
            jsonl_path=tmp_path / "run.jsonl",
            run_id="r",
            it=1,
            traj_dir=tmp_path / "traj",
            shard_dirs=[sd],
            commit="c" * 40,
            code_sha256="z" * 64,
            course="c",
            course_fp="f" * 64,
            reward_formula="score",
            formula_hash="h" * 40,
            metrics_version=1,
            gamma=0.995,
            lam=0.95,
            mode="per-tick",
            epochs=1,
            mb=512,
            lr=3e-4,
            init_weights_path=str(w),
            kind="iter",
            rollout_spec=spec,
            ts_code_sha256="t" * 64,
            log=lambda _m: None,
        )
    assert "不接受本地 shard" in str(e.value)


def test_publish_iter_needs_ts_code_sha(tmp_path: Path) -> None:
    from remote.hub_client import HubClientError

    with pytest.raises(HubClientError) as e:
        _publish_iter_missing_ts(tmp_path)
    assert "ts_code_sha256" in str(e.value)


def _publish_iter_missing_ts(tmp_path: Path) -> dict:
    from remote.hub_client import publish_job

    spec = validate_rollout_spec(_spec([(3, 7)]))
    w = tmp_path / "w.json"
    w.write_text("{}", encoding="utf-8")
    return publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=tmp_path / "run.jsonl",
        run_id="r",
        it=1,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course="c",
        course_fp="f" * 64,
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        init_weights_path=str(w),
        kind="iter",
        rollout_spec=spec,
        log=lambda _m: None,
    )


def test_verify_and_land_iter_uses_declared_data_fp(tmp_path: Path) -> None:
    """落位链：上云轮的 data_fp 对的是**声明值**（本地无 shard 可重算）。

    同时守着「声明值也要真的比」——节点报一个别的 data_fp 必须被拒。
    """
    import base64
    import io
    import tarfile as _tarfile

    from remote.hub_client import HubClientError, verify_and_land
    from remote.protocol import encode_opt_tar, encode_weights_json

    m, w = _publish_iter(tmp_path)
    buf = io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:") as tf:
        info = _tarfile.TarInfo("model.pt")
        blob = b"\x00" * 32
        info.size = len(blob)
        tf.addfile(info, io.BytesIO(blob))
    new_weights = b'{"format":"nn-weights-json","params":{"x":1}}'
    good = {
        "job_id": m["job_id"],
        "data_fp": m["data_fp"],
        "init_weights_fp": hashlib.sha256(w.read_bytes()).hexdigest(),
        "weights_json": encode_weights_json(new_weights),
        "opt_tar_b64": encode_opt_tar(buf.getvalue()),
        "commit_echo": m["commit"],
        "agg": {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "mean_ret": 0.0},
        "report": _iter_result()["report"],
    }
    out_w = tmp_path / "out" / "weights.json"
    verify_and_land(
        good,
        m,
        init_weights_path=str(w),
        traj_dir=str(tmp_path / "traj"),
        it=1,
        out_weights=str(out_w),
        log=lambda _m: None,
    )
    assert out_w.read_bytes() == new_weights
    assert (tmp_path / "traj" / "it1" / "ppo_ckpt_remote" / "model.pt").exists()
    # 声明值不符 → 拒收（不是「没有本地副本就跳过校验」）
    bad = dict(good, data_fp="0" * 64)
    with pytest.raises(HubClientError) as e:
        verify_and_land(
            bad,
            m,
            init_weights_path=str(w),
            traj_dir=str(tmp_path / "traj"),
            it=1,
            out_weights=str(out_w),
            log=lambda _m: None,
        )
    assert "data_fp" in str(e.value)
    # 声明集对应的 result.report 校验（协议层已经比过），这里补一条编码往返
    assert base64.b64decode(good["weights_json"])[:1] == b"\x1f"


# ------------------------------------------------------------------ 节点侧执行器

_STUB = """\
import json, sys
from pathlib import Path
a = sys.argv[1:]
def val(flag):
    return a[a.index(flag) + 1] if flag in a else ""
out = Path(val("--out"))
out.mkdir(parents=True, exist_ok=True)
stage, seed, wver = int(val("--stages")), int(val("--seeds")), val("--wver")
d = out.parent / f"rl_s{stage}_seed{seed}"
d.mkdir(parents=True, exist_ok=True)
(d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed, "wver": wver}))
(out / "_rl_report.json").write_text(json.dumps({
    "games": 1, "winRate": 1.0, "outcomes": {"stage_clear": 1},
    "totalSamples": 3, "totalTicks": 30, "scoreList": [1.0],
    "dimLists": {"kills": [2.0]},
}))
print("stub ok")
"""


def _stub_spec(tmp_path: Path, games: list[tuple[int, int]], **over) -> dict:
    """假 bun（本进程的 python）+ 桩 exporter —— 不依赖真 bun 地跑完整执行器。"""
    stub = tmp_path / "stub_exporter.py"
    stub.write_text(_STUB, encoding="utf-8")
    argv = []
    for i, (st, sd) in enumerate(games):
        argv.append([str(stub), "--out", f"w{i}", "--stages", str(st), "--seeds", str(sd),
                     "--wver", "W" * 64])
    s = {"argv": argv, "wver": "W" * 64, "workers": 2, "game_timeout_sec": 0.0,
         "bun": sys.executable}
    s.update(over)
    return s


def test_run_iter_rollout_with_stub_bun(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "9.9.9-stub")
    spec = _stub_spec(tmp_path, [(3, 7), (4, 1)])
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    out = run_iter_rollout(job_dir, spec, log=lambda _m: None)
    assert sorted(Path(p).name for p in out["shard_dirs"]) == [
        "rl_s3_seed7",
        "rl_s4_seed1",
    ]
    rep = out["report"]
    assert rep["games"] == 2 and rep["shards"] == 2
    assert rep["totalSamples"] == 6 and rep["totalTicks"] == 60
    assert rep["outcomes"] == {"stage_clear": 2}
    assert rep["winRate"] == 1.0
    assert rep["dimMeans"] == {"kills": 2.0}
    assert rep["rolloutSrc"] == "node"
    assert rep["elapsedSec"] >= 0.0
    assert out["bun_version"] == "9.9.9-stub"
    assert len(out["game_secs"]) == 2
    # 每局日志与报告都落盘（诊断口径与本机 rollout 同形）
    assert (job_dir / "w0" / "rollout.log").exists()
    assert json.loads((job_dir / "w0" / "_rl_report.json").read_text())["games"] == 1


def test_run_iter_rollout_rejects_undeclared_shard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    spec = _stub_spec(tmp_path, [(3, 7), (4, 1)])
    spec["argv"] = spec["argv"][:1]  # 声明一局，但桩会按 argv 跑——先删声明集外的
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    # 声明集 = 1 局，但目录里多出一个（模拟导出器多产/串局）
    _write_shard(job_dir / shard_name(9, 9), 9, 9)
    with pytest.raises(ProtocolError):
        run_iter_rollout(job_dir, spec, log=lambda _m: None)


def test_run_iter_rollout_reports_rc_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    spec = {
        "argv": [[str(bad), "--out", "w0", "--stages", "0", "--seeds", "0"]],
        "wver": "w",
        "workers": 1,
        "game_timeout_sec": 0.0,
        "bun": sys.executable,
    }
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    from remote.protocol import RetryableError

    with pytest.raises(RetryableError) as e:
        run_iter_rollout(job_dir, spec, log=lambda _m: None)
    assert "rc=3" in str(e.value)


def test_resolve_bun_missing_is_loud() -> None:
    with pytest.raises(ProtocolError) as e:
        iter_rollout.resolve_bun("definitely-not-a-real-binary-xyz")
    assert "找不到" in str(e.value)


# ------------------------------------------------------------------ 节点侧 TS 运行时缓存


def _ts_zip(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_ensure_ts_code_extracts_then_hits_cache(tmp_path: Path) -> None:
    """TS 运行时解包到内容寻址目录；第二次同 sha 直接命中（不再下载/解压）。

    这条缓存是本轮上云唯一「只付一次」的部分：命中失效 = 每轮多传一份 TS 树，
    而字节数又与 opt 同量级——且没有任何日志会显得不对劲。
    """
    from remote.worker import _ensure_ts_code

    raw = _ts_zip({"tools/sim/export-rl-rollout.ts": "// x", "src/a.ts": "// y"})
    sha = hashlib.sha256(raw).hexdigest()
    ts_root = tmp_path / "ts_code_cache"
    manifest = {"ts_code_sha256": sha}

    cache, n, hit = _ensure_ts_code(
        "http://unused",
        "tok",
        "j1",
        manifest,
        ts_root=ts_root,
        preloaded={"ts_code_zip": raw},
    )
    assert hit is False and n == len(raw)
    assert cache == ts_root / sha and (cache / "tools/sim/export-rl-rollout.ts").exists()

    # 第二轮：同 sha（不同 job）——不该再要字节，也不该再解一次
    cache2, n2, hit2 = _ensure_ts_code(
        "http://unused",
        "tok",
        "j2",
        manifest,
        ts_root=ts_root,
        preloaded=None,  # 命中就不该走到下载
        log=lambda _m: None,
    )
    assert hit2 is True and n2 == 0 and cache2 == cache
    assert not list(ts_root.glob("*.tmp"))  # 临时目录要么改名、要么清干净


def test_ensure_ts_code_sha_mismatch_is_retryable(tmp_path: Path) -> None:
    """sha 不符 = 传输损坏（瞬时）→ RetryableError（与 payload/code 同规）。"""
    from remote.worker import _ensure_ts_code

    manifest = {"ts_code_sha256": "0" * 64}
    with pytest.raises(RetryableError) as e:
        _ensure_ts_code(
            "http://unused",
            "tok",
            "j1",
            manifest,
            ts_root=tmp_path / "ts_code_cache",
            preloaded={"ts_code_zip": b"not-the-right-bytes"},
            log=lambda _m: None,
        )
    assert "ts_code_sha256 不匹配" in str(e.value)


def test_ensure_ts_code_requires_sha(tmp_path: Path) -> None:
    """manifest 缺 ts_code_sha256 → 响亮失败（不静默当成「没有 TS 运行时」）。"""
    from remote.worker import _ensure_ts_code

    with pytest.raises(ProtocolError) as e:
        _ensure_ts_code(
            "http://unused",
            "tok",
            "j1",
            {},
            ts_root=tmp_path / "ts_code_cache",
            preloaded=None,
            log=lambda _m: None,
        )
    assert "ts_code_sha256" in str(e.value)


# ------------------------------------------------------------------ 上云轮失败语义


class _IterStub(TrainingSteps):
    """承载 `_remote_iter` 的最小宿主（真远端要 hub/隧道/打包，这里只需失败分类）。"""

    def __init__(self, tmp_path: Path, exc: BaseException) -> None:
        w = tmp_path / "w.json"
        w.write_text("{}", encoding="utf-8")
        self.args = SimpleNamespace(
            target_transitions=0,
            out=str(w),
            remote_iter_workers=0,
            workers=1,
            remote_iter_game_timeout=0.0,
            course_path="",
            course="",
        )
        self.bun = "bun"
        self._traj_dir = tmp_path
        self._jsonl_path = tmp_path / "training_log.jsonl"
        self._jsonl_path.write_text("", encoding="utf-8")
        self._leg_abort = False
        self._node_rollout = True
        self._report: dict = {}
        self._node_rollout_sec: float | None = None
        self._rollout_sec = 0.0
        self.calls = 0
        self.exc = exc

    def _remote_ppo(
        self,
        it: int,
        rollout_spec: dict | None = None,
        *,
        plan_bytes: bytes | None = None,
        wait_timeout_sec: float = 0.0,
        export_path: str | Path | None = None,
    ) -> dict:
        self.calls += 1
        raise self.exc

    def events(self) -> list[dict]:
        return [
            json.loads(ln)
            for ln in self._jsonl_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]


def _stub_iter_spec(monkeypatch) -> None:
    """`build_iter_spec` 需要真课程对象——本用例只验失败分类，规格换桩。"""
    monkeypatch.setattr("rl.iter_job.build_iter_spec", lambda *a, **k: {"argv": [], "wver": "w"})


def test_remote_iter_fatal_http_aborts_without_retry(tmp_path: Path, monkeypatch) -> None:
    """401/403（鉴权/闭锁类）在**上云轮**也必须第一次就写 ABORT 停腿。

    上云轮不走 `_remote_ppo_or_degrade`（loop_core 在 `_node_rollout` 时跳过
    `_serial_ppo`），所以那条路的「4xx 立即停腿」得在 `_remote_iter` 里补上——否则
    x3-step 事故的同一浪费会重演：5×30s 重发同一个 job 才死。
    """
    from remote.hub_client import HubClientError

    _stub_iter_spec(monkeypatch)
    st = _IterStub(tmp_path, HubClientError('wait_job: HTTP 403: {"error": "ip blocked"}'))
    with pytest.raises(HubClientError):
        st._remote_iter(4, [(0, 0)])
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 4
    assert "HTTP 403" in ev[0]["reason"] and "node" in ev[0]["reason"]
    assert st._leg_abort is True
    assert st.calls == 1  # 一次就停，不重发


def test_remote_iter_transient_failure_stays_retryable(tmp_path: Path, monkeypatch) -> None:
    """网络抖动/超时：**不**写判决、不置停腿 —— 交给 loop 原地重试（既有语义）。"""
    from remote.hub_client import HubClientError

    _stub_iter_spec(monkeypatch)
    st = _IterStub(tmp_path, HubClientError("wait_job: HTTP 502: bad gateway"))
    with pytest.raises(HubClientError):
        st._remote_iter(4, [(0, 0)])
    assert st.events() == []
    assert st._leg_abort is False
    assert st.calls == 1


# ------------------------------------------------- 控制面迁移「最容易漏一半」的两处
#
# plan §5.4：上云轮的 shard 在节点上（跑完即毁），而本仓有两处会去数**本地** shard。
# 不排除就会拿「本地没有 shard」当成事故/空统计：前者每轮假告警（把真事故淹掉），
# 后者写一份 shards=0 的假精度统计。两处都已有显式跳过，这里锁住。


def test_iter_stats_and_quota_check_skip_on_node_rollout(
    tmp_path: Path, monkeypatch
) -> None:
    from rl import loop_steps as ls

    msgs: list[str] = []
    monkeypatch.setattr(ls, "log", lambda m: msgs.append(m))
    st = _IterStub(tmp_path, RuntimeError("unused"))
    st._node_rollout_sec = 12.5
    st._write_iter_stats(3)
    assert any("云节点" in m for m in msgs), msgs
    # 没写任何 jsonl（否则巡检页会出现一份 shards=0 的假统计）
    assert not (tmp_path / "metrics_stats.jsonl").exists()


def test_quota_incident_not_triggered_on_node_rollout(tmp_path: Path, monkeypatch) -> None:
    from rl import loop_core as lc
    from rl.loop_core import TrainingLoop

    msgs: list[str] = []
    monkeypatch.setattr(lc, "log", lambda m: msgs.append(m))
    args = SimpleNamespace(course_name="c4")
    # 被测方法只碰这三个属性 + args ⇒ 用 cast 声明「这是测试替身」
    # （照 test_remote_degrade.py 的 stub 口径，不构造真 TrainingLoop 的 torch 栈）。

    def _stub(node_rollout: bool) -> TrainingLoop:
        return cast(
            TrainingLoop,
            SimpleNamespace(
                _traj_dir=tmp_path,
                _node_rollout=node_rollout,
                _zero_shard_streak=0,
                args=args,
            ),
        )

    # 上云轮：本地零 shard 是**预期**（采集在节点），连续三轮也不该喊配额事故
    node = _stub(True)
    for it in (1, 2, 3):
        TrainingLoop._check_quota_incident(node, it)
    assert msgs == [] and node._zero_shard_streak == 0  # type: ignore[attr-defined]
    # 对照：本机轮行为逐字不变（连续 2 轮零 shard → 响亮告警）
    local = _stub(False)
    TrainingLoop._check_quota_incident(local, 1)
    assert msgs == []
    TrainingLoop._check_quota_incident(local, 2)
    assert any("连续 2 轮零 shard" in m for m in msgs), msgs

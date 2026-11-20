"""回归：replay 轮发布时，payload 的语料格式与 manifest 的自述必须是**同一个读数**。

2026-11-20 kaggle 真机（`[ppo] no RL shards found` 连炸 5 次、整段会话结束）：payload 已按
replay 瘦掉 obs（`payload_fmt=replay+reward+f16`），manifest 里却没有 `corpus_fmt`（缺键 =
full）⇒ worker 的 `prepass` 直接返回 None，不去重建 obs，转而读一份没有 obs 的语料。
根因：`5382ec80` 合上游时把发布相位拆成 `_remote_pack_inputs` 助手，`PublishSpec(...)` 里的
`corpus_fmt=` / `replay_converter_path=` 两行在冲突解决中被静默丢掉（打包侧仍读
`args.corpus_fmt`，于是两处各有一份真相）。

本文件钉住那两行存在且同源：
1. `_remote_pack_inputs` 的读数面自带这两项（与 `take(corpus_fmt=…)` 同一个局部量）；
2. `_remote_ppo_publish` 把它们原样交给 `PublishSpec`。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import remote.hub_client as hub_client
from remote import weights_standard
from trainer.loop_remote_job import PackInputs, TrainingRemoteJob


class _Course:
    """`_remote_ppo_publish` 拼 manifest 时只读课程里的这三样。"""

    reward = SimpleNamespace(formula="")

    def reward_spec(self) -> SimpleNamespace:
        return SimpleNamespace(identity=lambda: "")

    def ppo_schedule_dicts(self) -> list:
        return []


def _args(tmp: Path) -> SimpleNamespace:
    return SimpleNamespace(
        traj=str(tmp),
        out=str(tmp / "weights.json"),
        mode="rl",
        epochs=1,
        mb=1024,
        lr=3e-4,
        gamma=0.995,
        lam=0.95,
        corpus_fmt="replay",
        remote_slim=1,
        wire_diet=1,
        smoke=False,
        adv_norm="auto",
        normalize_ret=0,
        value_anchor=0.0,
    )


class _Publisher(TrainingRemoteJob):
    """发布编排：相位的每一步助手换成桩，`publish_job` 由用例接管。"""

    pack: PackInputs

    def __init__(self, tmp: Path, args: SimpleNamespace) -> None:
        self.args = args
        self._traj_dir = tmp
        self._jsonl_path = tmp / "log.jsonl"
        self._code_sha256 = "0" * 64
        self._code_zip_path = None

    def _remote_guard_plan(self, *a, **k) -> None:
        return None

    def _commit_journal(self) -> SimpleNamespace:
        return SimpleNamespace(start=lambda *a, **k: None)

    def _forensics(self, *a, **k) -> None:
        return None

    def _remote_transport(self, args) -> tuple:
        return ("http://hub", "tok", [], False, str(self._traj_dir))

    def _remote_course_and_shards(self, *a, **k) -> tuple:
        return (_Course(), "course", "cfp", "corpus", [], {})

    def _remote_code_snapshot(self, job_root) -> None:
        return None

    def _remote_kickstart_demo(self, args, it: int) -> tuple:
        return (False, 0.0, "", "", None, 0.0, 0)

    def _remote_pack_inputs(self, args, it: int, rollout_spec, job_root, mem) -> PackInputs:
        return self.pack

    def _per_stage_quota(self, it: int) -> int:
        return 0

    def _remote_after_publish(self, *a, **k) -> float:
        return 0.0


class _PackRig(TrainingRemoteJob):
    """只有 `_remote_pack_inputs` 是真的：段账与转换器构建换成桩。"""

    def __init__(self, tmp: Path, args: SimpleNamespace, conv: Path) -> None:
        self.args = args
        self._traj_dir = tmp
        self._conv = str(conv)
        self._payload_parts = SimpleNamespace(take=lambda *a, **k: None)

    def _ensure_replay_converter(self, job_root, *, log) -> tuple[str, str]:
        return self._conv, "sha"


def test_publish_spec_carries_replay_fmt_and_converter(tmp_path: Path, monkeypatch) -> None:
    """发布一个 replay 轮：`PublishSpec` 必须带语料格式与转换器路径（少一个 = manifest 缺键）。"""
    conv = tmp_path / "replay-to-shard.node.js"
    rig = _Publisher(tmp_path, _args(tmp_path))
    rig.pack = PackInputs(
        slim=True,
        wire_diet=True,
        presegmented={"rl_s1_seed1": b"PK"},
        diet_slim=None,
        corpus_fmt="replay",
        replay_converter_path=str(conv),
    )
    seen: dict = {}

    def _fake_publish(spec, *, log=None):
        seen["spec"] = spec
        return {"job_id": "jid-replay"}

    monkeypatch.setattr(hub_client, "publish_job", _fake_publish)
    monkeypatch.setattr(hub_client, "git_head", lambda: "deadbeef")

    rig._remote_ppo_publish(7)

    spec = seen["spec"]
    assert spec.corpus_fmt == "replay", "replay 轮的 manifest 会缺 corpus_fmt（worker 读成 full）"
    assert spec.replay_converter_path == str(conv), "转换器没进 PublishSpec ⇒ payload 里没有它"
    assert spec.presegmented == {"rl_s1_seed1": b"PK"}


def test_pack_inputs_reports_the_fmt_it_packed_with(tmp_path: Path, monkeypatch) -> None:
    """`_remote_pack_inputs` 返回的 `corpus_fmt` / 转换器路径必须与 `take()` 读的是同一个量。"""
    monkeypatch.setattr(weights_standard, "ensure_standard_weights", lambda *a, **k: None)
    conv = tmp_path / "replay-to-shard.node.js"
    rig = _PackRig(tmp_path, _args(tmp_path), conv)

    got = rig._remote_pack_inputs(rig.args, 7, None, str(tmp_path), {})

    assert got.corpus_fmt == "replay"
    assert got.replay_converter_path == str(conv)

    # full 轮：不建转换器，也不许把上一轮的路径漏进去。
    rig.args.corpus_fmt = "full"
    got_full = rig._remote_pack_inputs(rig.args, 7, None, str(tmp_path), {})
    assert got_full.corpus_fmt == "full"
    assert got_full.replay_converter_path == ""

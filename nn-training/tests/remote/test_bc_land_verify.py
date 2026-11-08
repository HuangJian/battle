"""BC 落位校验的 shard 口径（2026-11-08）。

fixed 源发布时用 `data_root=fixed_dir` + `*_s*_seed*` + D14 豁免；落位校验必须用同一口径，
否则它会在 `traj/bc-data/<round>` 扫 0 个 shard，算出空指纹 `e3b0c442…`，永远拒收。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.protocol import PROTO, data_fp, encode_weights_json
from remote.hub_client import verify_and_land_bc
from remote.hub_http import HubClientError


def _write_shard(root: Path, name: str, *, stage: int, seed: int) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "wver": "w" * 64,
                "stage": stage,
                "seed": seed,
                "course_fp": "f" * 64,
            }
        ),
        encoding="utf-8",
    )
    return d


def _manifest(fp: str) -> dict:
    return {
        "proto": PROTO,
        "kind": "bc",
        "runId": "bc-land-test",
        "it": 1,
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "{}",
        "course_fp": "f" * 64,
        "mode": "bc",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 8,
        "lr": 3e-4,
        "arch": "bc",
        "init_weights_fp": "i" * 64,
        "data_fp": fp,
        "payload_sha256": "p" * 64,
        "corpus_fp": "",
    }


def _result(fp: str) -> dict:
    return {
        "init_weights_fp": "i" * 64,
        "data_fp": fp,
        "commit_echo": "c" * 40,
        "weights_json": encode_weights_json(b'{"ok":true}'),
    }


def test_fixed_source_lands_against_its_own_root(tmp_path: Path) -> None:
    fixed = tmp_path / "rollouts"
    dirs = [
        _write_shard(fixed, "rl_s0_seed1", stage=0, seed=1),
        _write_shard(fixed, "rl_s1_seed2", stage=1, seed=2),
    ]
    fp = data_fp(dirs)
    out = tmp_path / "weights.json"

    wver = verify_and_land_bc(
        _result(fp),
        _manifest(fp),
        traj_dir=tmp_path / "traj",
        it=1,
        out_weights=str(out),
        data_root=fixed,
        fixed_source=True,
        log=lambda _m: None,
    )

    assert out.read_bytes() == b'{"ok":true}'
    assert len(wver) == 64


def test_collect_source_still_lands_from_default_round_dir(tmp_path: Path) -> None:
    traj = tmp_path / "traj"
    round_dir = traj / "bc-data" / "it1"
    dirs = [
        _write_shard(round_dir, "bc_s0_seed1", stage=0, seed=1),
        _write_shard(round_dir, "bc_s1_seed2", stage=1, seed=2),
    ]
    fp = data_fp(dirs)
    out = tmp_path / "weights.json"

    verify_and_land_bc(
        _result(fp),
        _manifest(fp),
        traj_dir=traj,
        it=1,
        out_weights=str(out),
        log=lambda _m: None,
    )

    assert out.read_bytes() == b'{"ok":true}'


def test_fixed_source_without_root_hint_is_rejected_loudly(tmp_path: Path) -> None:
    """旧口径（不传 data_root）必须拒收，而不是静默算出空指纹。"""
    fixed = tmp_path / "rollouts"
    dirs = [_write_shard(fixed, "rl_s0_seed1", stage=0, seed=1)]
    fp = data_fp(dirs)

    with pytest.raises(HubClientError):
        verify_and_land_bc(
            _result(fp),
            _manifest(fp),
            traj_dir=tmp_path / "traj",
            it=1,
            out_weights=str(tmp_path / "weights.json"),
            log=lambda _m: None,
        )

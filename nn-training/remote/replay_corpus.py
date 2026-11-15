"""remote/replay_corpus.py — worker 侧 replay → obs/scalars/extra 的 pre-pass（P2）。

判据（plan/replay-corpus.plan.md D3/D5）：

* 转换器 bundle 由 **trainer 预构建**、随 kind=ppo job 的 payload 根下发（`replay_converter.js`），
  worker **只跑不建**（2 vCPU 不付 `bun build`）；manifest 带 `replay_converter_sha256`。
* 世界重建与校验全部在转换器内完成（obsHash / tickHash / decisionTicks / 终局）；
  本模块只负责「找到运行时 → 逐 shard 跑 → 断言产物齐全 → 写完成标记」。
* **确定性失败零容差**：任一 shard 转换失败 = ProtocolError（重试无意义），
  不允许跳过该 shard 静默训少一局。
* **可重试/可续跑**：每个 shard 成功后在目录里写 `replay_converted.json`
  （converter sha + replay sha）；重启时只复用「标记 + 产物齐全 + sha 匹配」的目录。
  转换器自身是 `.tmp` + rename 原子写，崩溃后重跑会覆盖半成品。
* 运行时：优先 `node`（bundle 是 `--target=node`）；没有 Node 才用 `bun`；两者皆无 = 响亮失败。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from common.errors import ProtocolError
from common.proc import run_capture

CONVERTER_NAME = "replay_converter.js"
MARKER_NAME = "replay_converted.json"
MARKER_VERSION = 1
#: 单 shard 转换上界（秒）。一局最多 ~36000 tick；实测 600 tick 约 60ms，
#: 36000 tick 约 3.6s；900s 是「机器假死」与「真慢」的分界。
SHARD_TIMEOUT_SEC = 900.0


def resolve_js_runtime() -> str:
    """转换器运行时：Node 优先（bundle 目标），Bun 兜底；都没有 = 响亮失败。"""
    for name in ("node", "bun"):
        found = shutil.which(name)
        if found:
            return found
    raise ProtocolError(
        "replay 语料需要 Node ≥22 或 Bun 运行预建转换器，但本机两者都没有"
        "（plan/replay-corpus.plan.md D5：Bun 失败回落 Node；两者皆无即失败）"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_converter(job_dir: str | Path, manifest: dict) -> tuple[Path, str]:
    """校验 payload 根下发的转换器：文件在 + sha 与 manifest 一致。"""
    p = Path(job_dir) / CONVERTER_NAME
    want = str(manifest.get("replay_converter_sha256", "") or "")
    if not want:
        raise ProtocolError(
            "corpus_fmt=replay 的 job 缺 manifest.replay_converter_sha256——无法判定转换器身份，拒收"
        )
    if not p.is_file():
        raise ProtocolError(f"corpus_fmt=replay 的 payload 缺 {CONVERTER_NAME}——拒收")
    got = _sha256_file(p)
    if got != want:
        raise ProtocolError(
            f"{CONVERTER_NAME} sha 不符（payload={got[:12]}… manifest={want[:12]}…）——拒收"
        )
    return p, want


def _marker_ok(shard_dir: Path, converter_sha: str) -> bool:
    """完成标记 + 三张产物齐全 + 转换器 sha 匹配 ⇒ 可复用。"""
    marker = shard_dir / MARKER_NAME
    if not marker.is_file():
        return False
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(meta, dict) or int(meta.get("version", -1)) != MARKER_VERSION:
        return False
    if str(meta.get("converter_sha256", "")) != converter_sha:
        return False
    return all((shard_dir / n).is_file() for n in ("obs.npy", "scalars.npy", "extra.npy"))


def convert_shard(
    shard_dir: Path,
    *,
    converter: Path,
    converter_sha: str,
    runtime: str,
    timeout_sec: float = SHARD_TIMEOUT_SEC,
) -> bool:
    """转换一个 shard；返回 True = 复用标记（未跑子进程）。失败抛 ProtocolError。"""
    if _marker_ok(shard_dir, converter_sha):
        return True
    # 标记在但产物不全/转换器变了 ⇒ 删标记重跑（产物由转换器覆盖）。
    marker = shard_dir / MARKER_NAME
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass
    try:
        # `run_capture` 自带显式 UTF-8 + Windows 无黑窗（AGENTS §19 / POPEN_NO_WINDOW）。
        p = run_capture(
            [runtime, str(converter), "--shard-dir", str(shard_dir)],
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        raise ProtocolError(
            f"replay 转换超时（{shard_dir.name}，>{timeout_sec:.0f}s）—— 确定性失败，不重试"
        ) from e
    except OSError as e:
        raise ProtocolError(f"replay 转换器起不来（{runtime}）：{e}") from e
    if p.returncode != 0:
        raise ProtocolError(
            f"replay 转换失败（{shard_dir.name}，rc={p.returncode}）："
            f"{(p.stderr or p.stdout)[-800:]}"
        )
    missing = [n for n in ("obs.npy", "scalars.npy", "extra.npy") if not (shard_dir / n).is_file()]
    if missing:
        raise ProtocolError(
            f"replay 转换器返回 0 但产物缺失（{shard_dir.name}: {missing}）——拒收"
        )
    replay = shard_dir / "replay.json"
    meta = {
        "version": MARKER_VERSION,
        "converter_sha256": converter_sha,
        "replay_sha256": _sha256_file(replay) if replay.is_file() else "",
    }
    tmp = shard_dir / (MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(marker)
    return False


def convert_replay_corpus(
    job_dir: str | Path,
    shard_dirs: list[Path],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    workers: int = 2,
) -> dict[str, Any]:
    """把一整轮 replay shard 就地转成 obs/scalars/extra，返回读数。"""
    if not shard_dirs:
        raise ProtocolError("corpus_fmt=replay 但 payload 里没有任何 shard——拒收（不训空）")
    converter, conv_sha = verify_converter(job_dir, manifest)
    runtime = resolve_js_runtime()
    t0 = time.time()
    reused = 0

    def _one(sd: Path) -> bool:
        return convert_shard(
            sd, converter=converter, converter_sha=conv_sha, runtime=runtime
        )

    n = max(1, min(int(workers), len(shard_dirs)))
    with ThreadPoolExecutor(max_workers=n) as ex:
        for got in ex.map(_one, shard_dirs):
            if got:
                reused += 1
    sec = time.time() - t0
    log(
        f"replay 转换完成：{len(shard_dirs)} shard（复用 {reused}），"
        f"{sec:.1f}s runtime={Path(runtime).name} converter={conv_sha[:12]}…"
    )
    return {
        "shards": len(shard_dirs),
        "reused": reused,
        "sec": round(sec, 1),
        "converter_sha256": conv_sha,
        "runtime": Path(runtime).name,
    }


def prepass(
    job_dir: str | Path,
    shard_dirs: list[Path],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    bundle: Any = None,
) -> dict[str, Any] | None:
    """`run_job` 的 replay pre-pass 入口：非 replay 原样返回 None（零行为变化）。

    kind 闸在这里而不是散在宿主里：replay 只允许 kind=ppo（节点自跑 rollout 的 kind=iter
    强制 full，同机没有 wire 收益）。
    """
    if str(manifest.get("corpus_fmt") or "full") != "replay":
        return None
    if str(manifest.get("kind")) != "ppo":
        raise ProtocolError(
            f"corpus_fmt=replay 只支持 kind=ppo，收到 kind={manifest.get('kind')!r}"
            "——kind=iter 的节点自跑 rollout 强制 full（plan/replay-corpus.plan.md D2）"
        )
    got = convert_replay_corpus(job_dir, shard_dirs, manifest, log=log)
    if bundle is not None:
        bundle.add(
            "replay 转换",
            f"{got['sec']}s → {got['shards']} shard（复用 {got['reused']}）",
        )
    return got


def attach_converter(
    extra_files: list[Path], tmp_dir: str | Path, path: str | Path
) -> str:
    """把预构建转换器放进 payload 根（`publish_job` 的 extra_files），返回 sha256。"""
    if not str(path or "") or not Path(path).is_file():
        raise ProtocolError(
            f"corpus_fmt=replay 必须带 trainer 预构建的转换器（收到 {path!r}）——拒发"
        )
    raw = Path(path).read_bytes()
    copy = Path(tmp_dir) / CONVERTER_NAME
    copy.write_bytes(raw)
    extra_files.append(copy)
    return hashlib.sha256(raw).hexdigest()


def attach_converter_for(
    extra_files: list[Path],
    tmp_dir: str | Path,
    corpus_fmt: str,
    path: str | Path,
    manifest: dict,
) -> None:
    """按请求格式挂转换器（full = 一步不做，manifest/payload 逐字节旧行为）。"""
    if str(corpus_fmt or "full") != "replay":
        return
    manifest["replay_converter_sha256"] = attach_converter(extra_files, tmp_dir, path or "")


__all__ = [
    "CONVERTER_NAME",
    "MARKER_NAME",
    "attach_converter",
    "attach_converter_for",
    "convert_replay_corpus",
    "convert_shard",
    "prepass",
    "resolve_js_runtime",
    "verify_converter",
]

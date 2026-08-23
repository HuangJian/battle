"""
dist_common.py — 分布式采样 trainer 侧公共工具（stdlib-only，可脱离 torch 独立测试）。

与 tools/agent/sampler-agent.ts 构成双语协议契约（plan/distributed-rollout.md v3.3）：

- codeHash 配方（两侧实现必须逐字节一致）：对 glob 集（`src/nn/**` 全部文件 +
  `tools/sim/export-rl-rollout.ts`）按 posix 相对路径字典序遍历，依次喂入
  sha256(path 字节) 与 sha256(文件内容)，最终 hex。
- 结果容器：gzip(JSON {manifest, files:{name: base64}})；files 恰为 12 个 npy，
  manifest 为单局 _rl_report.json 内容 + elapsedSec/node 等溯源字段。
- 权重下发：POST body = gzip(weights.json 字节)，头部 X-Iter-Id / X-Weights-Sha256；
  agent 校验 sha 一致后，同 sha 幂等不动、异 sha 原子切换并清空结果缓存。

红线：远端结果必须先过 validate_result() 再落进 traj_dir —— discover_rl_shards()
对已落盘目录是无条件递归扫描的，落盘之后没有任何兜底。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "nn-training", "dist-nodes.json")

SHARD_FILES = (
    "obs.npy", "scalars.npy", "a_move.npy", "a_fire.npy", "a_item.npy",
    "lp_move.npy", "lp_fire.npy", "lp_item.npy", "value.npy", "reward.npy",
    "done.npy", "mask.npy",
)


class DistError(RuntimeError):
    """节点交互失败：status=HTTP 状态码（0=本地校验拒绝），reason=可读原因。"""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"HTTP {status}: {reason}" if status else reason)
        self.status = status
        self.reason = reason


def load_dist_config(path: str = CONFIG_PATH) -> dict | None:
    """每轮分派点调用一次（动态读取）；文件缺失/损坏返回 None（纯本地模式）。

    用 utf-8-sig：运维在 Windows 上用 PowerShell 改 JSON 常带 BOM，必须容忍。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"[dist] WARN: cannot parse {path}: {e} — falling back to local-only")
        return None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("nodes"), list):
        return None
    return cfg


def weights_fingerprint(path: str) -> str:
    """sha256(weights.json 文件字节) —— 版本过滤键（语义：样本确由该权重产生）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_code_hash() -> str:
    entries = _collect_code_hash_files()
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for rel, content in entries:
        h.update(rel.encode())
        h.update(hashlib.sha256(content).digest())
    return h.hexdigest()


def _collect_code_hash_files() -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    nn_root = os.path.join(REPO_ROOT, "src", "nn")
    for dirpath, _dirs, files in os.walk(nn_root):
        for name in files:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, REPO_ROOT).replace("\\", "/")
            with open(p, "rb") as f:
                out.append((rel, f.read()))
    rollout = os.path.join(REPO_ROOT, "tools", "sim", "export-rl-rollout.ts")
    if os.path.exists(rollout):
        with open(rollout, "rb") as f:
            out.append(("tools/sim/export-rl-rollout.ts", f.read()))
    return out


# ---------------- HTTP ----------------
def _request(url: str, auth_key: str, timeout: float, data: bytes | None = None,
             headers: dict[str, str] | None = None, method: str | None = None):
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {auth_key}", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def node_ping(url: str, auth_key: str, timeout: float = 3.0) -> dict | None:
    """GET /v1/ping → dict；任何失败返回 None（调用侧决定排除该节点）。"""
    try:
        status, body = _request(url.rstrip("/") + "/v1/ping", auth_key, timeout)
        if status == 200:
            return json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return None


def post_weights(url: str, auth_key: str, iter_id: str, sha: str, weights_bytes: bytes,
                 timeout: float = 120.0) -> str:
    """POST /v1/weights → 'kept' | 'purged'；失败抛 DistError。"""
    status, body = _request(
        url.rstrip("/") + "/v1/weights", auth_key, timeout,
        data=gzip.compress(weights_bytes),
        headers={"Content-Encoding": "gzip", "X-Iter-Id": iter_id, "X-Weights-Sha256": sha},
        method="POST",
    )
    if status not in (200, 204):
        raise DistError(status, body[:300].decode("utf-8", "replace"))
    try:
        info = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        info = {}
    return str(info.get("cache", "kept"))


def fetch_task(url: str, auth_key: str, *, iter_id: str, wver: str, stage: int, seed: int,
               max_ticks: int, difficulty: str, timeout: float) -> tuple[dict, dict]:
    """GET /v1/task → (manifest, files)；非 200 抛 DistError。"""
    qs = urllib.parse.urlencode({
        "iterId": iter_id, "wver": wver, "stage": stage, "seed": seed,
        "maxTicks": max_ticks, "difficulty": difficulty,
    })
    try:
        status, body = _request(url.rstrip("/") + "/v1/task?" + qs, auth_key, timeout)
    except urllib.error.HTTPError as e:
        raise DistError(e.code, e.read()[:300].decode("utf-8", "replace")) from e
    if status != 200:
        raise DistError(status, body[:300].decode("utf-8", "replace"))
    container = json.loads(gzip.decompress(body.lstrip(b" \t\r\n")).decode("utf-8"))
    if not isinstance(container, dict) or not isinstance(container.get("files"), dict):
        raise DistError(0, "container missing files map")
    return container.get("manifest") or {}, container["files"]


# ---------------- 结果校验（先验后落盘的红线所在） ----------------
def validate_result(manifest: dict, files: dict, expected_wver: str,
                    expected_pairs: set[tuple[int, int]],
                    seen_keys: set[tuple[int, int]]) -> str | None:
    """返回 None=通过；否则给出拒收原因。"""
    if not isinstance(manifest, dict):
        return "manifest is not an object"
    if manifest.get("wver") != expected_wver:
        return f"wver mismatch: got {manifest.get('wver')!r}"
    key = (manifest.get("stage"), manifest.get("seed"))
    if key not in expected_pairs:
        return f"unexpected (stage,seed)={key}"
    if key in seen_keys:
        return f"duplicate (stage,seed)={key}"
    if set(files.keys()) != set(SHARD_FILES):
        extra = sorted(set(files) - set(SHARD_FILES))
        lack = sorted(set(SHARD_FILES) - set(files))
        return f"file set mismatch (extra={extra}, missing={lack})"
    for name, b64 in files.items():
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return f"{name}: invalid base64"
        if len(raw) == 0:
            return f"{name}: empty payload"
    return None


def write_shard(files: dict, manifest: dict, out_dir: str) -> None:
    """校验通过后的唯一落盘出口：目录名沿用 rl_s{si}_seed{seed} 布局。"""
    os.makedirs(out_dir, exist_ok=True)
    for name in SHARD_FILES:
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(base64.b64decode(files[name], validate=True))
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

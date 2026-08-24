"""
dist_common.py — 分布式采样 trainer 侧公共工具（stdlib-only，可脱离 torch 独立测试）。

与 tools/agent/sampler-agent.ts 构成双语协议契约（plan/distributed-rollout.md v3.6）：

- codeHash 配方（两侧实现必须逐字节一致）：对 glob 集（`src/nn/**` 全部文件 +
  `tools/sim/export-rl-rollout.ts`）按 posix 相对路径字典序遍历，依次喂入
  sha256(path 字节) 与 sha256(文件内容)，最终 hex。
- 结果容器：
  - v2（BCV2，v3.6 起 agent 缺省）：gzip( magic u32 | headerLen u32 | headerJSON |
    entry* )，entry = nameLen u16 | name | dataLen u64 | 原始 npy 字节——由 exporter
    子进程打包，无 base64。files 值为 bytes。
  - v1（旧 agent 兼容）：gzip(JSON {manifest, files:{name: base64}})。files 值为 str。
  解码端 unpack_container() 按 magic 自动识别，validate/write_shard 双模兼容。
- 任务获取：fetch_task() 带 x-async 头提交——新 agent 立即 202+token，转 /v1/result
  轮询（瞬断可重试，结果在 agent 缓存里不丢）；旧 agent 忽略该头同步返回整包，
  行为与 v3.5 完全一致。两种响应在同一函数内消化。
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
import struct
import time
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


def unpack_container(raw: bytes) -> tuple[dict, dict]:
    """解结果容器（自动识别版本）→ (manifest, files)。

    v2（BCV2）files 值为 bytes；v1（旧 agent）值为 base64 str——下游
    validate_result/write_shard 双模兼容，勿假设其中一种。
    """
    frame = gzip.decompress(raw.lstrip(b" \t\r\n"))
    if len(frame) >= 8:
        (magic,) = struct.unpack_from(">I", frame, 0)
        if magic == PACK_MAGIC:
            return _unpack_bcv2(frame)
    container = json.loads(frame.decode("utf-8"))
    if not isinstance(container, dict) or not isinstance(container.get("files"), dict):
        raise DistError(0, "container missing files map")
    return container.get("manifest") or {}, container["files"]


# BCV2 魔数 'B''C''V''2' —— 与 tools/sim/pack-container.ts 逐字节一致的双语契约。
PACK_MAGIC = 0x42435632


def _unpack_bcv2(frame: bytes) -> tuple[dict, dict]:
    off = 4
    (hlen,) = struct.unpack_from(">I", frame, off)
    off += 4
    header = json.loads(frame[off : off + hlen].decode("utf-8"))
    off += hlen
    manifest = header.get("manifest") or {}
    files: dict[str, bytes] = {}
    for spec in header.get("files") or []:
        (nlen,) = struct.unpack_from(">H", frame, off)
        off += 2
        name = frame[off : off + nlen].decode("utf-8")
        off += nlen
        (dlen,) = struct.unpack_from(">Q", frame, off)
        off += 8
        if name != spec.get("name") or dlen != spec.get("len"):
            raise DistError(0, f"bcv2 entry mismatch: {name!r} (header said {spec!r})")
        files[name] = frame[off : off + dlen]
        off += dlen
    return manifest, files


def fetch_task(url: str, auth_key: str, *, iter_id: str, wver: str, stage: int, seed: int,
               max_ticks: int, difficulty: str, timeout: float,
               mode: str | None = None) -> tuple[dict, dict]:
    """获取一局结果 → (manifest, files)；失败抛 DistError。

    mode='eval' 请求干净评估局（agent 端贪心 runner、无 shards）；仅对 ping 返回
    evalSupport=true 的节点使用——旧 agent 会静默忽略该参数跑成采样局。

    v3.6：提交带 x-async 头。新 agent 立即 202 → 转 /v1/result 轮询（轮询期网络瞬断
    不丢局：结果在 agent 结果缓存里，恢复后继续拉）；旧 agent 无视该头同步阻塞返回
    整包（与 v3.5 行为逐字节一致）。注意 submit 必须用完整 timeout——对旧 agent 而言
    这就是原来的长连接等待，短超时会把同步模式误杀。
    """
    params = {
        "iterId": iter_id, "wver": wver, "stage": stage, "seed": seed,
        "maxTicks": max_ticks, "difficulty": difficulty,
    }
    if mode:
        params["mode"] = mode
    qs = urllib.parse.urlencode(params)
    base = url.rstrip("/")
    started = time.monotonic()
    try:
        status, body = _request(f"{base}/v1/task?{qs}", auth_key, timeout,
                                headers={"x-async": "1"})
        if status == 202:
            return _poll_result(base, auth_key, params, timeout - (time.monotonic() - started))
        if status == 200:
            return unpack_container(body)
        raise DistError(status, body[:300].decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise DistError(e.code, e.read()[:300].decode("utf-8", "replace")) from e
    except DistError:
        raise
    except Exception as e:
        # 提交期网络错误 + 轮询期逃逸的非 DistError（deadline 时的 socket 超时、
        # 损坏容器解包错）统一包装；真实原因保留在 message 里。
        raise DistError(0, f"task fetch failed: {e}") from e


def _poll_result(base_url: str, auth_key: str, params: dict, budget: float,
                 poll_s: float = 3.0) -> tuple[dict, dict]:
    """轮询 GET /v1/result 直到取包/失败/超时。budget 秒内传输瞬断一律重试。

    只有网络调用本身受瞬断重试保护；容器解包与非预期状态码是确定性错误，
    必须立即抛出真实原因——绝不能被重试逻辑吞成误导性的 deadline exceeded。
    """
    qs = urllib.parse.urlencode({
        "iterId": params["iterId"], "stage": params["stage"], "seed": params["seed"],
    })
    deadline = time.monotonic() + max(1.0, budget)
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise DistError(0, "async result deadline exceeded (game still running on node?)")
        try:
            status, body = _request(f"{base_url}/v1/result?{qs}", auth_key,
                                    timeout=min(20.0, remain))
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code == 500:
                # 局失败已被 agent 一次性消费；trainer 照常回队重试（可能换节点）
                try:
                    msg = json.loads(detail).get("error", detail)
                except ValueError:
                    msg = detail
                raise DistError(500, str(msg)) from e
            if e.code == 404:
                raise DistError(404, f"task lost on node (restart/purge): {detail}") from e
            raise DistError(e.code, detail) from e
        except Exception:
            # 瞬断（休眠/SSH 重连/隧道抖动）：结果仍在节点缓存里，睡一下继续拉。
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(poll_s, max(0.5, deadline - time.monotonic())))
            continue
        if status == 200:
            return unpack_container(body)
        if status == 202:
            time.sleep(min(poll_s, remain))
            continue
        raise DistError(status, body[:300].decode("utf-8", "replace"))


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
    for name, val in files.items():
        try:
            # v2 容器值为原始 bytes；v1 旧 agent 值为 base64 str——双模兼容。
            raw = val if isinstance(val, (bytes, bytearray)) \
                else base64.b64decode(val, validate=True)
        except Exception:
            return f"{name}: invalid base64"
        if len(raw) == 0:
            return f"{name}: empty payload"
    return None


def validate_eval_result(manifest: dict, expected_wver: str) -> str | None:
    """干净评估局的轻量校验：无 shards，仅对账 wver、模式回显与关键字段。"""
    if not isinstance(manifest, dict):
        return "manifest is not an object"
    if manifest.get("wver") != expected_wver:
        return f"wver mismatch: got {manifest.get('wver')!r}"
    if manifest.get("mode") != "eval":
        return f"mode echo mismatch: got {manifest.get('mode')!r} (old agent ran sampled game?)"
    for k in ("outcome", "ticks", "win", "stage", "seed"):
        if k not in manifest:
            return f"missing field {k!r}"
    return None


def write_shard(files: dict, manifest: dict, out_dir: str) -> None:
    """校验通过后的唯一落盘出口：目录名沿用 rl_s{si}_seed{seed} 布局。"""
    os.makedirs(out_dir, exist_ok=True)
    for name in SHARD_FILES:
        val = files[name]
        raw = val if isinstance(val, (bytes, bytearray)) \
            else base64.b64decode(val, validate=True)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

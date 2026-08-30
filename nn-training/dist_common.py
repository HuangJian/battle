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
CONFIG_PATH = os.path.join(REPO_ROOT, "nn-training", "rl-config.json")

SHARD_FILES = (
    "obs.npy", "scalars.npy", "a_move.npy", "a_fire.npy",
    "lp_move.npy", "lp_fire.npy", "value.npy", "reward.npy",
    "done.npy", "mask.npy",
)

# M8 意图 RL shard 清单（export-intent-rollout.ts 产物）——意图步 semi-MDP：
# inject（prev one-hot 8 + duration）与 dt（窗口时长 tick，变步长 GAE 用）替换
# a_move/a_fire/lp_move/lp_fire；mask 为 8 类死类掩码。
INTENT_SHARD_FILES = (
    "obs.npy", "scalars.npy", "inject.npy", "a_intent.npy", "lp_intent.npy",
    "value.npy", "reward.npy", "done.npy", "mask.npy", "dt.npy",
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
    # 2026-08-30：agent 本体入 codeHash（与 sampler-agent.ts 的 TS 侧同集）——
    # agent 的协议/桶/重启行为修复必须触发节点自动升级。
    agent_self = os.path.join(REPO_ROOT, "tools", "agent", "sampler-agent.ts")
    if os.path.exists(agent_self):
        with open(agent_self, "rb") as f:
            out.append(("tools/agent/sampler-agent.ts", f.read()))
    rollout = os.path.join(REPO_ROOT, "tools", "sim", "export-rl-rollout.ts")
    if os.path.exists(rollout):
        with open(rollout, "rb") as f:
            out.append(("tools/sim/export-rl-rollout.ts", f.read()))
    # M8：意图 RL 分布式 rollout 走 export-intent-rollout.ts —— 入 codeHash，
    # 保证节点代码同步（意图步采样语义与 per-tick 完全不同）。
    intent_rollout = os.path.join(REPO_ROOT, "tools", "sim", "export-intent-rollout.ts")
    if os.path.exists(intent_rollout):
        with open(intent_rollout, "rb") as f:
            out.append(("tools/sim/export-intent-rollout.ts", f.read()))
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


# 训练机当前分支（run_rl.py 启动时锁存）。节点远控升级**永远**用这个分支——
# rl-config 的 upgradeBranch 已废弃（残留旧战役分支名曾把全部节点 reset 回
# 旧代码，2026-08-30 事故）。queue/eval_dispatch 传来的 branch 参数为空时用它。
UPGRADE_BRANCH: str | None = None


def set_upgrade_branch(branch: str) -> None:
    global UPGRADE_BRANCH
    UPGRADE_BRANCH = branch


def is_self_node(url: str, node_id: str = "") -> bool:
    """self/回环节点：agent 由训练机同一工作区启动——它的代码就是训练机代码，
    远控 upgrade 会在共享工作区做破坏性 pull（2026-08-30 事故：本机被 reset 回
    旧代码）。此类节点只参与派单，**永不发升级请求**（代码不同步时仅排除该轮，
    由操作者手动重启）。"""
    if node_id.strip().lower() == "self":
        return True
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def upgrade_branch_or(explicit: str | None) -> str:
    if explicit:
        return explicit
    return UPGRADE_BRANCH or ""


def request_upgrade(url: str, auth_key: str, branch: str, timeout: float = 20.0) -> bool:
    """主动升级机制（M8）：POST /v1/restart {pullBranch} → agent 端 git pull + 重启。

    编排层 ping 发现节点 codeHash 不符（stale）时调用——把它从「静默排除」升级为
    「主动指示更新重启」。返回 True = 接受（agent 异步执行，重启窗口内不可达，
    rescan 会在它恢复后按新 codeHash 纳入）。失败返回 False（不抛、不阻塞训练）。
    """
    try:
        status, _body = _request(
            url.rstrip("/") + "/v1/restart", auth_key, timeout,
            data=json.dumps({"pullBranch": branch}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return status in (200, 202)
    except Exception:
        return False


def upgrade_stale_nodes(cfg: dict, expected_hash: str, branch: str,
                        status_timeout: float = 3.0, restart_timeout: float = 20.0,
                        max_nodes: int = 16) -> list[dict]:
    """主动升级扫描：ping 每个 enabled 节点，codeHash ≠ expected（stale）→ request_upgrade。

    返回 [{id, upgraded, reason}]（诊断用）。不可达/失败跳过——升级机制是 best-effort，
    绝不阻塞本轮 dispatch（stale 节点本轮仍不参与，待重启后由 rescan 纳入）。
    """
    out: list[dict] = []
    for n in cfg.get("nodes", [])[:max_nodes]:
        if not n.get("enabled", True):
            continue
        nid = str(n.get("id") or n.get("url") or "?")
        ping = node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
        if ping is None:
            out.append({"id": nid, "upgraded": False, "reason": "unreachable"})
            continue
        if ping.get("codeHash") == expected_hash:
            out.append({"id": nid, "upgraded": False, "reason": "current"})
            continue
        upgraded = request_upgrade(n["url"], n.get("authKey", ""), branch,
                                   timeout=restart_timeout)
        out.append({"id": nid, "upgraded": upgraded,
                    "reason": "stale" if upgraded else "stale-upgrade-failed",
                    "agentVersion": ping.get("agentVersion")})
    return out


def post_weights(url: str, auth_key: str, iter_id: str, sha: str, weights_bytes: bytes,
                 timeout: float = 120.0, kind: str = "rollout") -> str:
    """POST /v1/weights → 'kept' | 'purged'；失败抛 DistError。

    kind（v3.7/M8）：'rollout'（per-tick RL 采样）/ 'intent'（意图权重桶——
    intent-exec 评估 + 意图 RL rollout 共用）。agent 按 x-kind 分桶缓存。
    """
    status, body = _request(
        url.rstrip("/") + "/v1/weights", auth_key, timeout,
        data=gzip.compress(weights_bytes),
        headers={"Content-Encoding": "gzip", "X-Iter-Id": iter_id,
                 "X-Weights-Sha256": sha, "X-Kind": kind},
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
               mode: str | None = None,
               kind: str = "rollout",
               replan: int = 0,
               reward: str = "",
               dodge: str = "") -> tuple[dict, dict]:
    """获取一局结果 → (manifest, files)；失败抛 DistError。

    mode='eval' 请求干净评估局（agent 端贪心 runner、无 shards）；仅对 ping 返回
    evalSupport=true 的节点使用——旧 agent 会静默忽略该参数跑成采样局。

    kind（M8）：'intent' 请求意图权重桶（意图 RL rollout 走 export-intent-rollout.ts）。
    replan（M8）：意图 rollout 的 replan cadence（0=不传）。
    reward（goal-nn 卡 A2）：玩具奖励臂覆盖（''=不传，导出器按 stage 解析默认）。
    dodge（goal-nn 卡 A3）：dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。

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
    if kind != "rollout":
        params["kind"] = kind
    if replan > 0:
        params["replan"] = replan
    if reward:
        params["reward"] = reward
    if dodge:
        params["dodge"] = dodge
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
    qparams = {
        "iterId": params["iterId"], "stage": params["stage"], "seed": params["seed"],
    }
    # mode/kind 必须与提交端 key 配方一致（agent 的 result key = iterId:mode:kind:stage:seed）——
    # 意图 rollout（kind=intent）不传则轮询落空 404（实测教训）。
    if params.get("mode"):
        qparams["mode"] = params["mode"]
    if params.get("kind"):
        qparams["kind"] = params["kind"]
    qs = urllib.parse.urlencode(qparams)
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
def _shard_files_for(manifest: dict) -> tuple:
    """意图 RL shard（collector=INTENT-RL）用 INTENT_SHARD_FILES，否则 per-tick SHARD_FILES。"""
    if manifest.get("collector") == "INTENT-RL" or "a_intent.npy" in manifest:
        return INTENT_SHARD_FILES
    return SHARD_FILES


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
    want = _shard_files_for(manifest)
    if set(files.keys()) != set(want):
        extra = sorted(set(files) - set(want))
        lack = sorted(set(want) - set(files))
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
    for name in _shard_files_for(manifest):
        val = files[name]
        raw = val if isinstance(val, (bytes, bytearray)) \
            else base64.b64decode(val, validate=True)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

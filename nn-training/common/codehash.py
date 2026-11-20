"""common/codehash.py — 代码身份（codeHash）：清单展开、字节配方、节点门判据。

与 `tools/agent/sampler-agent.ts` / `codehash-files.ts` 构成双语契约：两侧读**同一份**
`tools/agent/codehash-files.txt`，按同一规则展开，按同一顺序喂 sha256（先路径字节、再内容
字节）。任何一侧硬编码文件清单都会重新引入 2026-09-01 那次 stale 事故（本地改了、节点算不到）。

从 `common/distribution.py` 拆出来（2026-11-16，B8 第一刀）：这里只有文件 IO 与哈希，
不发一个 HTTP 请求 —— 分发层的探活 / 下发 / 任务面与它无关，混在一个 1600 行模块里时，
「codeHash 是什么」要翻过十几个网络函数才找得到。

三条判据都在这里，不要在外面重写：
  * `compute_code_hash` —— 值本身（`ping.codeHash` 与本地期望值比的都是它）；
  * `check_code_hash` —— 节点门（rollout 与 eval 同一个门，拒收原因是一句话）；
  * `dirty_hash_files` —— 工作区字节 ≠ git 索引内容的集合（非空 ⇒ 远端 pull 不可能收敛）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess

from common.paths import REPO_ROOT
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from common.proc import run_capture


def compute_code_hash() -> str:
    entries = _collect_code_hash_files()
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for rel, content in entries:
        h.update(rel.encode())
        h.update(hashlib.sha256(content).digest())
    return h.hexdigest()


CODE_HASH_MANIFEST = os.path.join(REPO_ROOT, "tools", "agent", "codehash-files.txt")


def _skip_codehash_dir(name: str) -> bool:
    """目录递归时的跳过规则（F3，plan/dist-codehash-stale-fix.md）：任一路径段以
    '.' 开头（隐藏项 .git/.DS_Store/.venv）或名为 __pycache__ / node_modules。
    与 tools/agent/codehash-files.ts isSkippedCodeHashDir 逐条对齐——契约写死在
    codehash-files.txt 头部注释，任一侧改动必须双侧同步。"""
    return name.startswith(".") or name in ("__pycache__", "node_modules")


def _skip_codehash_file(name: str) -> bool:
    """文件级跳过规则（F3）：隐藏项（. 开头）+ 编辑器/构建临时后缀。
    与 tools/agent/codehash-files.ts isSkippedCodeHashFile 逐条对齐。"""
    return name.startswith(".") or name.endswith(
        (".pyc", ".orig", ".rej", ".bak", ".tmp", ".log", ".swp", "~")
    )


def _collect_code_hash_files() -> list[tuple[str, bytes]]:
    """按 SSOT 清单 codehash-files.txt 展开 codeHash 文件集（与 sampler-agent.ts 同源）。

    清单每行一个条目：'#' 注释 / 空行忽略；以 '/' 结尾 = 目录（递归纳入其下所有
    文件，目录条目受 F3 噪声过滤）；其余 = 具体文件（相对 repo 根、posix 路径；
    不存在则跳过，单文件条目不过滤）。两侧读同一清单、按同一规则展开，杜绝单侧
    硬编码漂移（2026-09-01 事故教训）。
    """
    out: list[tuple[str, bytes]] = []
    try:
        with open(CODE_HASH_MANIFEST, encoding="utf-8") as f:
            specs = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return out
    for spec in specs:
        spec = spec.replace("\\", "/")
        if spec.endswith("/"):
            root = os.path.join(REPO_ROOT, *spec.rstrip("/").split("/"))
            for dirpath, dirnames, files in os.walk(root):
                # F3：跳过隐藏/缓存目录（剪枝不递归），再按文件规则过滤。
                dirnames[:] = [d for d in dirnames if not _skip_codehash_dir(d)]
                for name in files:
                    if _skip_codehash_file(name):
                        continue
                    p = os.path.join(dirpath, name)
                    rel = os.path.relpath(p, REPO_ROOT).replace("\\", "/")
                    with open(p, "rb") as f:
                        out.append((rel, f.read()))
        else:
            p = os.path.join(REPO_ROOT, *spec.split("/"))
            if os.path.isfile(p):
                rel = os.path.relpath(p, REPO_ROOT).replace("\\", "/")
                with open(p, "rb") as f:
                    out.append((rel, f.read()))
    return out


def code_hash_report() -> str:
    """codeHash 诊断报告（F4，plan/dist-codehash-stale-fix.md）：与
    tools/agent/codehash-report.ts 同格式的 TSV，供双侧 diff 定位 stale 差异。

    每行 `sha8\tsize\trelPath`（按 relPath 排序），末行 `codeHash=<full>`。
    用法：python -c "import common.distribution;print(common.distribution.code_hash_report())"
    """
    entries = _collect_code_hash_files()
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    lines: list[str] = []
    for rel, content in entries:
        h.update(rel.encode())
        h.update(hashlib.sha256(content).digest())
        lines.append(f"{hashlib.sha256(content).digest().hex()[:8]}\t{len(content)}\t{rel}")
    lines.append(f"codeHash={h.hexdigest()}")
    return "\n".join(lines)


# ---------------- 节点门指纹：唯一事实来源 = SSOT 清单（回 2026-09-17 用户指令） ----------------
# rollout 门（dispatch / rescan）与 eval 门（eval_dispatch / batch_eval）现在比的是
# **同一个值 = codeHash**（展开自 tools/agent/codehash-files.txt；引擎 src/game、config、
# RNG、God AI 已并入该清单）。先例（engine_epoch 掺 git commit）会让任何与 rollout/eval
# 无关的提交（dashboard / nn-training / docs）把全节点判 stale、逼运维重启 sampler-agent。
def compute_engine_epoch() -> str:
    """engine_epoch = sha256(codeHash)[:16]（codeHash 见 SSOT 清单；纯函数）。

    **这是账本记录值，不是节点门判据**（节点门比 codeHash，见 check_code_hash）：
    它进 EvalGameRow.engine / 心跳，供 S10 记录级漂移哨兵与跨轮可比性断言用。
    """
    return hashlib.sha256(compute_code_hash().encode()).hexdigest()[:16]


def check_code_hash(ping: dict, expected: str) -> str | None:
    """节点可用性判据（rollout 与 eval 同一门）：None=通过，否则拒收原因。

    2026-09-17：eval 侧改比 codeHash（原先比 ping.engineEpoch）——该字段已从
    /v1/ping 移除，engine_epoch 退为账本记录值。唯一事实来源 = SSOT 清单
    （tools/agent/codehash-files.txt），与 rollout 无关的提交不会让本门变红。
    """
    got = ping.get("codeHash")
    if not got:
        return "missing codeHash (old agent — sync code + restart)"
    if got != expected:
        return f"codeHash mismatch: node={str(got)[:8]} expected={expected[:8]}"
    return None


# --------------------------------------------------------------- git 脏文件（dirty hash）


def _parse_porcelain(text: str) -> list[str]:
    """git status --porcelain v1 输出 → 路径列表（含改名目标、去引号）。"""
    out: list[str] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        # 格式：XY<space>PATH；改名行形如 "R  old -> new"
        p = line[3:].split(" -> ", 1)[-1].strip().strip('"')
        if p:
            out.append(p)
    return out


def _git_index_blobs(rels: list[str]) -> dict[str, str]:
    """索引中 rel → blob sha 映射；未跟踪路径不出现在结果里。"""
    if not rels:
        return {}
    # 显式 UTF-8：`text=True` 单独用会按 locale 解码（zh-CN Windows = cp936），
    # 中文路径/非 ASCII 会静默丢输出（docs/nn/engineering.md §19）。走 run_capture 统一。
    proc = run_capture(["git", "ls-files", "-s", "--", *rels], cwd=REPO_ROOT, timeout=15)
    if proc.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        meta, raw_path = line.split("\t", 1)
        fields = meta.split()
        if len(fields) < 2:
            continue
        out[raw_path.strip().strip('"').replace("\\", "/")] = fields[1]
    return out


def _git_cat_blobs(shas: list[str]) -> dict[str, bytes]:
    """批量取 blob 原始字节（git cat-file --batch）；缺失的 sha 不进结果。"""
    if not shas:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=REPO_ROOT,
        input=("\n".join(shas) + "\n").encode(),
        capture_output=True,
        timeout=30,
        **_POPEN_NO_WINDOW,
    )
    out: dict[str, bytes] = {}
    buf = proc.stdout
    pos = 0
    while pos < len(buf):
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        parts = buf[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(parts) < 3:
            continue  # "<sha> missing"
        oid, size = parts[0], int(parts[2])
        out[oid] = buf[pos : pos + size]
        pos += size + 1
    return out


def dirty_hash_files() -> list[str]:
    """codeHash 覆盖集中「工作区字节 ≠ git 索引内容」的文件（含未跟踪文件）。

    非空 ⇒ 期望 codeHash 含 pull 拿不到的本地字节，远端 git pull 永远无法收敛——
    此时对远端节点下发 restart+pull 只会产生无效扰动（拉到同样的提交、hash 依旧
    不等 → 下轮再杀，无限重启循环）。返回空列表 = 集内代码与索引逐字节一致。

    判据用**原始字节 vs 索引 blob 字节**，不用 git status：core.autocrlf=input 会把
    工作区 CRLF 规范化成 LF 再比对，「工作区 CRLF / 索引 LF」这类纯 EOL 污染被完全
    隐藏（2026-09-09 mac 事故：git status 干净、dirty=[]，但 codeHash 与远端干净
    checkout 只差一个 src/nn/wasm/conv_feats.c 的 CRLF，节点卡了 40 分钟）。

    未跟踪文件同样计为 dirty——远端 pull 不会带出该文件。git 不可用/出错时返回 []
    （保守放行）——跨代去重护栏仍兜底防循环。
    """
    entries = _collect_code_hash_files()
    if not entries:
        return []
    try:
        idx = _git_index_blobs([rel for rel, _ in entries])
        blobs = _git_cat_blobs(sorted(set(idx.values())))
    except (OSError, subprocess.SubprocessError, ValueError):
        # git 不可用 / 超时（`TimeoutExpired` 是 `SubprocessError`）/ 输出格式变了（`int()` 失败）：
        # 一律保守放行（`dirty=[]`），跨代去重护栏仍兜底防循环。别的错（本函数自己的 bug）照抛。
        return []
    dirty: list[str] = []
    for rel, content in entries:
        sha = idx.get(rel)
        if sha is None or blobs.get(sha) != content:
            dirty.append(rel)
    return dirty

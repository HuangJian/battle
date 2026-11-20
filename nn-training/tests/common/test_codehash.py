"""`common/codehash.py` 的契约：文件清单展开 / dirty 判据（autocrlf 盲区）/ 报告格式 / 双语对拍。

原住 `tests/common/test_upgrade.py`（与「远控升级」混在一起）；2026-11-19 升级那半搬去
`tests/remote/test_node_upgrade.py`（模块也搬了家），这里只留 codeHash 面，并顺手把
`check()` + `FAILS` 的脚本体改成真断言 —— 旧写法在 pytest 下判失败也不红。
"""

from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import common.codehash
from common.paths import REPO_ROOT
from common.platform_utils import rmtree_best_effort
from tests.subproc_util import run_utf8

#: 仓库根（bun 侧脚本在 `tools/agent/` 下，与 `tests/subproc_util` 同一约定）。
REPO = Path(__file__).resolve().parents[3]


def test_parse_porcelain() -> None:
    """`_parse_porcelain`：修改/暂存/未跟踪/改名（含引号路径）/空行。"""
    text = (
        " M src/types.ts\n"
        "M  src/nn/rl-reward-toy.ts\n"
        "?? tools/agent/restart-guard.ts\n"
        'R  old_name.ts -> "new name.ts"\n'
        "\n"
    )
    got = common.codehash._parse_porcelain(text)
    assert got == [
        "src/types.ts",
        "src/nn/rl-reward-toy.ts",
        "tools/agent/restart-guard.ts",
        "new name.ts",
    ], got


def test_dirty_hash_files_smoke() -> None:
    """真实仓库上不抛异常、返回 list。"""
    d = common.codehash.dirty_hash_files()
    assert isinstance(d, list), f"dirty_hash_files 返回 list（不抛）, got {type(d).__name__}"


def test_dirty_hash_files_eol_blindspot() -> None:
    """dirty 判据必须穿透 autocrlf：工作区**原始字节** vs 索引 blob 逐字节比对。

    回归 2026-09-09 mac 事故：src/nn/wasm/conv_feats.c 工作区 CRLF / 索引 LF，
    git status（core.autocrlf=input 会先规范化再比较）判为干净 ⇒ dirty=[] ⇒
    训练机期望 codeHash 与远端干净 checkout 不等，节点卡了 40 分钟零贡献。
    """
    real = common.codehash._collect_code_hash_files()
    assert real, "codeHash 文件集非空（前置条件）"
    rel, content = real[0]
    orig = common.codehash._collect_code_hash_files
    try:
        common.codehash._collect_code_hash_files = lambda: [(rel, content)]
        assert common.codehash.dirty_hash_files() == [], "工作区字节=索引 → clean"
        # 仅行尾差异（语义等价、字节不同）必须判 dirty——git status 看不见它
        common.codehash._collect_code_hash_files = lambda: [(rel, content.replace(b"\n", b"\r\n"))]
        assert common.codehash.dirty_hash_files() == [rel], "纯 CRLF 污染 → dirty（autocrlf 盲区）"
        # 未跟踪文件（远端 pull 拿不到）同样 dirty
        common.codehash._collect_code_hash_files = lambda: [("no/such/untracked.ts", b"x")]
        assert common.codehash.dirty_hash_files() == ["no/such/untracked.ts"], "未跟踪 → dirty"
    finally:
        common.codehash._collect_code_hash_files = orig


def test_codehash_f3_noise_filtering() -> None:
    """F3（plan/dist-codehash-stale-fix.md）：目录递归受噪声过滤——.DS_Store /
    __pycache__ / x.pyc / 编辑器临时后缀不计入；合法 .ts/.wasm 保留；显式单文件
    条目不受过滤（契约：只对目录递归生效）。fixture 在 tmp/pytest-tmp 内临时造
    （禁止污染仓库），测试后清理。"""
    real_manifest = common.codehash.CODE_HASH_MANIFEST
    base = REPO_ROOT / "tmp" / "pytest-tmp" / f"chfix-f3-{os.getpid()}-{int(time.time() * 1000)}"
    fixture = base / "src" / "nn"
    (fixture / "wasm").mkdir(parents=True, exist_ok=True)
    (fixture / "conv.ts").write_text("x", encoding="utf-8")
    (fixture / "wasm" / "conv_feats.wasm").write_bytes(b"wasm")
    (fixture / ".DS_Store").write_bytes(b"\x00")
    (fixture / ".editorconfig").write_text("x", encoding="utf-8")
    (fixture / "__pycache__").mkdir(exist_ok=True)
    (fixture / "__pycache__" / "infer.cpython-310.pyc").write_bytes(b"pyc")
    (fixture / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (fixture / "node_modules" / "pkg" / "index.ts").write_text("x", encoding="utf-8")
    (fixture / "x.pyc").write_bytes(b"pyc")
    (fixture / "x.orig").write_text("x", encoding="utf-8")
    (fixture / "x.rej").write_text("x", encoding="utf-8")
    (fixture / "x.bak").write_text("x", encoding="utf-8")
    (fixture / "x.tmp").write_text("x", encoding="utf-8")
    (fixture / "x.log").write_text("x", encoding="utf-8")
    (fixture / "x.swp").write_text("x", encoding="utf-8")
    (fixture / "x~").write_text("x", encoding="utf-8")
    rel_dir = os.path.relpath(fixture, REPO_ROOT).replace("\\", "/")
    manifest = base / "codehash-files.txt"
    # 目录条目（受过滤）+ 显式单文件条目（不过滤）+ 不存在条目（跳过）。
    manifest.write_text(
        f"# fixture\n{rel_dir}/\n{rel_dir}/x.tmp\ntmp/definitely-missing/ignored.ts\n",
        encoding="utf-8",
    )
    try:
        common.codehash.CODE_HASH_MANIFEST = str(manifest)
        entries = common.codehash._collect_code_hash_files()
        rels = [rel for rel, _c in entries]
        included = sorted(r for r in rels if r.startswith(rel_dir))
        want = sorted(
            [
                f"{rel_dir}/conv.ts",
                f"{rel_dir}/wasm/conv_feats.wasm",
                f"{rel_dir}/x.tmp",  # 显式单文件条目不过滤
            ]
        )
        assert included == want, f"F3 过滤后只留合法文件, got {included}"
        for bad in (
            ".DS_Store",
            ".editorconfig",
            "x.pyc",
            "x.orig",
            "x.rej",
            "x.bak",
            "x.log",
            "x.swp",
            "x~",
            "__pycache__",
            "node_modules",
        ):
            assert not any(r == f"{rel_dir}/{bad}" for r in rels), f"目录递归过滤 {bad}"
    finally:
        common.codehash.CODE_HASH_MANIFEST = real_manifest
        rmtree_best_effort(base, ignore_errors=True)


def test_code_hash_report() -> None:
    """F4：`code_hash_report()` 输出格式——每行 `sha8\\tsize\\trelPath`，末行
    `codeHash=<full>`；行数 = 文件数 + 1。与 codehash-report.ts 同格式（双侧
    diff 定位 stale 的前提）。"""
    report = common.codehash.code_hash_report()
    lines = report.splitlines()
    n_files = len(common.codehash._collect_code_hash_files())
    assert len(lines) == n_files + 1, f"报告行数 = 文件数+1 (got {len(lines)} vs {n_files}+1)"
    assert lines[-1].startswith("codeHash="), f"末行 codeHash=<full>, got {lines[-1][:20]!r}"
    if n_files:
        first = lines[0].split("\t")
        assert len(first) == 3 and len(first[0]) == 8 and first[1].isdigit(), (
            f"首行 sha8\\tsize\\trelPath, got {lines[0]!r}"
        )


def test_codehash_manifest_expansion() -> None:
    """SSOT 清单 codehash-files.txt 展开：目录条目递归、文件条目直接纳入，relPath 全
    正斜杠且无重复；本次事故的 3 个关键文件必须在集内。"""
    entries = common.codehash._collect_code_hash_files()
    rels = [rel for rel, _content in entries]
    assert len(rels) > 10, f"清单展开非空（got {len(rels)} files）"
    # 2026-09-01 事故：Python 侧加了这 3 个文件、TS 侧漏同步 → 节点被永久误判 stale。
    for need in (
        "tools/agent/restart-guard.ts",
        "src/types.ts",
        "src/game/SimulationCombat.ts",
        "tools/agent/sampler-agent.ts",
    ):
        assert need in rels, f"清单含 {need}"
    assert any(r.startswith("src/nn/") for r in rels), "src/nn/ 目录条目递归纳入"
    # 2026-09-17：引擎/God/评估文件并入本清单（eval 门与 rollout 门同源，
    # 原 GAMEPLAY_SPECS 双份清单已删）——改引擎必须改 codeHash。
    for need2 in ("tools/det-golden.v1.sha256", "src/game/SimulationCombat.ts"):
        assert need2 in rels, f"清单含 {need2}"
    for spec in ("src/game/", "src/config/", "src/utils/", "src/ai/"):
        assert any(r.startswith(spec) for r in rels), f"清单递归纳入 {spec}"
    # 与 rollout/eval 无关的树严禁入集：入集 = 它们的每次提交都触发节点重启波。
    assert not any(r.startswith(("dashboard/", "nn-training/")) for r in rels), (
        "无关树（dashboard/nn-training）不入集"
    )
    backslashed = [r for r in rels if "\\" in r]
    assert not backslashed, f"relPath 全 posix 正斜杠：{backslashed[:3]}"
    assert len(rels) == len(set(rels)), "文件集无重复"


def _parse_code_hash_files(stdout: str) -> tuple[set[str], str]:
    """`--print-code-hash-files` 的输出 → `(文件集, codeHash 行)`（**只认形态，不认行号**）。

    文件行 = 恰有两列制表福分隔的三字段（`hash\\tsize\\trelpath`）；末行 = `codeHash=<full>`。

    ★ 为什么不能按「除末行外都是文件行」解析（2026-10-10 修满载 flake）：agent 的 stdout 上
    还会落**引导期日志**（如 `[11:55:19] [sampler-agent] workdir swept @boot (5 stale entries
    removed)`），位置在文件行之前；满载门禁下原实现 `ln.split("\\t")[2]` 在那种行上 IndexError
    ⇒ 用例偶发红（单跑 3 次红 1 次；裸命令 5 次里复现 1 次）。日志行不应该让**文件集对拍**失效，
    但也不能静默忽略：这里把「认不出的行」统一交给调用方（本函数只挑出可识别的两类）。
    """
    files: set[str] = set()
    hash_line = ""
    for raw in stdout.splitlines():
        ln = raw.rstrip("\r")
        if ln.startswith("codeHash="):
            hash_line = ln
            continue
        parts = ln.split("\t")
        if len(parts) == 3:
            files.add(parts[2])
    return files, hash_line


def test_code_hash_files_parser_tolerates_boot_log_lines() -> None:
    """解析器自身的判据（不依赖 bun/网络）：混进引导期日志行也不得丢行或炸。"""
    clean = "aaaa\t100\tsrc/a.ts\nbbbb\t200\tsrc/b.ts\ncodeHash=deadbeef\n"
    files, hash_line = _parse_code_hash_files(clean)
    assert files == {"src/a.ts", "src/b.ts"} and hash_line == "codeHash=deadbeef"

    # 复现现场：boot 日志在文件行**之前**（原实现在这里 IndexError）
    noisy = "[11:55:19] [sampler-agent] workdir swept @boot (5 stale entries removed)\n" + clean
    assert _parse_code_hash_files(noisy) == (files, hash_line)

    # 日志插在文件行**中间**也不得影响集合
    interleaved = (
        "aaaa\t100\tsrc/a.ts\n"
        "[11:55:19] [sampler-agent] keepalive tick\n"
        "bbbb\t200\tsrc/b.ts\n"
        "[11:55:20] [sampler-agent] done\n"
        "codeHash=deadbeef\n"
    )
    assert _parse_code_hash_files(interleaved) == (files, hash_line)
    # 真的丢了行的输入必须看得见（解析器不替 agent 保证完整性）
    assert _parse_code_hash_files("no tabs here\ncodeHash=x\n")[0] == set()


def test_codehash_bilingual_contract() -> None:
    """双语 codeHash 契约：TS 侧（节点）与 Python 侧（训练机）算出的 hash 必须逐字节
    一致。任一侧漂移（文件集/内容/排序/归一化）都会红。bun 不可用则跳过。"""
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("bun 不可用，跳过双语契约比对")
    agent = REPO / "tools" / "agent" / "sampler-agent.ts"
    # 两次 bun 调用互不依赖（同一条命令的两个读数），而每次都是「bun 启动 + 全仓 hash」——
    # 串行跑把同一份启动付两遍（实测 3.2s 里约 1.5s 是第二次启动）。并发起，断言顺序不变；
    # Python 侧 hash 在本进程算，正好与两个子进程重叠（它们在外面跑，不吃 GIL）。
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_hash = pool.submit(
            run_utf8, [bun, str(agent), "--print-code-hash"], cwd=str(REPO), timeout=120
        )
        f_files = pool.submit(
            run_utf8, [bun, str(agent), "--print-code-hash-files"], cwd=str(REPO), timeout=120
        )
        py_hash = common.codehash.compute_code_hash()
        try:
            proc = f_hash.result()
        except Exception as e:
            pytest.fail(f"bun --print-code-hash 执行失败: {e}")
    assert proc.returncode == 0, f"bun --print-code-hash 非零退出: {proc.stderr.strip()}"
    # --print-code-hash 输出带 [HH:MM:SS] 时间戳前缀，取最后一个 token。
    ts_hash = proc.stdout.strip().split()[-1]
    assert len(ts_hash) == 64 and ts_hash == py_hash, (
        f"双语 codeHash 一致 (TS={ts_hash[:12]}… Python={py_hash[:12]}…)"
    )
    # F3/F4 文件集逐文件对拍（2026-09-01 事故防线：hash 相同但集不同由这里兜底）——
    # --print-code-hash-files 输出与 Python 侧 _collect_code_hash_files 全等。
    proc2 = f_files.result()
    assert proc2.returncode == 0, f"bun --print-code-hash-files 非零退出: {proc2.stderr.strip()}"
    # 行形态由 `_parse_code_hash_files` 认（boot 日志行会落在 stdout 上，别按行号解析）
    ts_files, hash_line = _parse_code_hash_files(proc2.stdout)
    py_files = {rel for rel, _c in common.codehash._collect_code_hash_files()}
    assert ts_files == py_files, f"双侧文件集逐文件一致 (TS={len(ts_files)} vs Python={len(py_files)})"
    assert hash_line.startswith("codeHash="), f"--print-code-hash-files 的 codeHash 行, got {hash_line[:20]!r}"

"""拆分的**契约守卫**：下载簇永住 `remote/download.py`（S4 第七刀，2026-09-23）。

搬走的组（252 行）：`_progress_logger` · `download_payload` · `download_code` ·
`download_ts_code` · `download_blob` · `_cache_blob` · `_resolve_blob` · `_ensure_ts_code`。
`remote/worker.py` 2579 → 2348 行。

**S4 第十二刀（2026-09-24）扩到「物料落地三兄弟」**：`_ensure_payload`（payload → 清场重建
`work_dir/<jid>/` + 解 shard）与 `_ensure_code`（code.zip → `code_cache/<sha>/` + `sys.path[0]`）
从 `run_job` 整段搬进本模块，与原有的 `_ensure_ts_code` 并列。`worker.py` **1039 → 1033**
（净减不多：搬走 69 行，换回的是三兄弟的**返回值解包**与两条转发注释）；`download.py` 313 → 519。

本文件钉六件事：

1. **定义唯一**——不许在 `worker.py` 里再实现一遍；
2. **依赖方向**——`download` 只许依赖 `common.protocol` / `remote.http` / `remote.wire` /
   `remote.bulk_sched`（全向下），不得 import `remote.worker`；
3. **转发同一对象**；
4. **顶层无新增可变容器**；
5. ✭ **注入点分档（本刀的主坑）**：`_resolve_blob` 调 `download_blob` 是**组内互调**，
   在本模块命名空间解析 ⇒ 必须 patch `remote.download`；而**宿主**（`run_job` /
   `_prefetch_fill`）调的 `download_*` 仍按**裸名字**解析在**各自所在的宿主模块** ⇒
   `run_job` ⇒ patch `remote.worker`；`_prefetch_fill` ⇒ patch `remote.job_round`
   （S4 第十刀把它搬走了，本文件的宿主断言随之分成两档）。
   两个方向各一条断言，防「patch 打偏而测试全绿」。
   **第十二刀后 `run_job` 不再是 `download_*` 的宿主**：物料落地整段进了本模块
   ⇒ `payload` / `code` 两个下载函数也归「组内」档，`run_job` 只读三个 `_ensure_*`。
6. **`_progress_logger` 仍恰好两份**（本模块 + `remote/tailscale_boot.py`）——它是**有意的孪生**
   （tailscale_boot 要独立拉取），谁再抄第三份就红。
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.download as download_mod
import remote.worker as worker_mod
from common.protocol import BLOB_OPT, ProtocolError, RetryableError
from tests.helpers import remote_dag as dag
from tests.helpers import source_scan

DL_FILE = ROOT / "remote" / "download.py"
WORKER_FILE = ROOT / "remote" / "worker.py"
#: S4 第十刀后 `_prefetch_fill` 住这里（它也是 `download_*` 的一个宿主调用点）。
ROUND_FILE = ROOT / "remote" / "job_round.py"

MOVED_NAMES = {
    "_cache_blob",
    "_ensure_code",
    "_ensure_payload",
    "_ensure_ts_code",
    "_progress_logger",
    "_resolve_blob",
    "download_blob",
    "download_code",
    "download_payload",
    "download_ts_code",
}
#: 物料落地三兄弟（宿主 `run_job` 按裸名字读它们 ⇒ 它们是**活**的 `remote.worker` 转发名）。
LANDING_NAMES = ("_ensure_payload", "_ensure_code", "_ensure_ts_code")
ALLOWED_IMPORTS = {
    "common.protocol",
    "remote.bulk_sched",
    "remote.course_cache",
    "remote.http",
    "remote.job_fs",
    "remote.wire",
}


def _tree(path: Path) -> ast.Module:
    return source_scan.parse(str(path))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_moved_names_are_defined_in_download_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `download.py` 里实现。"""
    assert _defined(DL_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(DL_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_download_only_depends_downwards_and_stays_within_its_whitelist() -> None:
    """无环 + 白名单：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得 import `remote.worker`」+ 本地白名单；现在交付给共用实现：
    顶层 intra-remote 边严格向下 · 任何 import 不得碰 `rl` · 顶层仓内依赖 ⊆ `ALLOWED_IMPORTS`。
    """
    dag.assert_remote_module("remote.download", allowed_project_imports=ALLOWED_IMPORTS)


def test_worker_forwards_every_moved_name_as_the_same_object() -> None:
    """宿主与测试都从 `remote.worker` 取这些名字 ⇒ 必须是同一对象。"""
    for name in MOVED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(download_mod, name), (
            f"remote.worker.{name} 不是 remote.download.{name}（转发成了副本）"
        )


def test_download_has_no_top_level_mutable_container() -> None:
    """搬走的是函数簇（零模块级状态）——顶层不该多出可变容器。"""
    mutable: set[str] = set()
    for node in _tree(DL_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == set(), f"download.py 顶层多了可变容器：{sorted(mutable)}"


def test_intra_module_seam_is_the_download_module(monkeypatch, tmp_path: Path) -> None:
    """✭ 组内互调：`_resolve_blob` → `download_blob` 解析在 **`remote.download`**。

    「把 `remote.worker.download_blob` 换成炸弹、`remote.download.download_blob` 返回真字节」
    必须仍然成功——反过来（只 patch worker）就是本步最容易犯的静默错误。
    """
    raw = b"opt-bytes"
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        worker_mod, "download_blob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(download_mod, "download_blob", lambda *a, **k: raw)
    got, hit, src = download_mod._resolve_blob(
        blob_root=tmp_path / "blob_cache",
        name=BLOB_OPT,
        sha=sha,
        inline_b64="",
        jid="j",
        base_url="http://hub",
        token="t",
        preloaded=None,
        log=lambda _m: None,
    )
    assert got == raw and hit is False and src == "download"


def test_empty_base_url_is_deterministic_and_never_retried() -> None:
    """★ 2026-10-04 现场回归（x20-adv-acc）：base_url 为空 = 确定性配置错误，一次都别重试。

    旧行为：urllib 抛 `ValueError: unknown url type: '/jobs/…/blob?name=opt'`，被当瞬时抖动
    退避重试 3 次（白等 6s+、日志引导不到根因），整段在 PPO 之前失败。现在当场 ProtocolError
    且正文点名最常见的两条修法（包/锚点没带 blob 字节 / 在线腿没给 --hub-url）。
    """
    from remote.download import download_blob

    calls: list[str] = []
    with pytest.raises(ProtocolError, match="没有 hub 地址"):
        download_blob(
            "",
            "tok",
            "j" * 16,
            BLOB_OPT,
            log=calls.append,
        )
    assert calls == [], "确定性失败不该留下「2s 后重试」这类日志（一次都不重试）"


def _bare_loads(path: Path, names: set[str]) -> dict[str, set[str]]:
    """这些宿主函数各自把模块全局当**裸名字**读的全部名字（AST `Load` = 引用即接缝）。"""
    loads: dict[str, set[str]] = {}
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            loads[node.name] = {
                n.id
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
    return loads


def test_host_callers_still_resolve_their_own_host_namespace() -> None:
    """宿主侧相反档位：裸名字解析在**调用点所在的模块**。

    * `run_job`（`worker.py`）现在只读**物料落地三兄弟**——`download_payload` /
      `download_code` 的调用点已随第十二刀进了本模块 ⇒ 对它们 patch `remote.worker` 会**静默失效**；
    * `_prefetch_fill`（第十刀搬到 `remote/job_round.py`）仍读 `download_payload` ⇒
      `tests/remote/test_soft_hold_prefetch.py` 要 patch `remote.job_round`。

    若哪天某一处改成 `download.download_payload(...)`（属性访问），对应的 patch 会静默失效——
    本断言就是那个警报。
    """
    host = _bare_loads(WORKER_FILE, {"run_job"})
    assert set(host) == {"run_job"}, f"worker.py 的 `download_*` 宿主变了：{set(host)}"
    for name in LANDING_NAMES:
        assert name in host["run_job"], f"run_job 不再读 {name}（落地搬走了吗？）"
    for name in ("download_payload", "download_code"):
        assert name not in host["run_job"], (
            f"run_job 又直接读 {name} 了——物料落地应当只在 remote.download 里发生"
        )

    fill = _bare_loads(ROUND_FILE, {"_prefetch_fill"})
    assert set(fill) == {"_prefetch_fill"}, f"job_round.py 里没有 `_prefetch_fill`：{set(fill)}"
    assert "download_payload" in fill["_prefetch_fill"]

    # 且两处都**没有**属性式访问（否则就是换了命名空间）
    for path in (WORKER_FILE, ROUND_FILE):
        text = source_scan.read_text(str(path))
        assert "download.download_" not in text and "download_mod.download_" not in text, path.name


def test_run_job_no_longer_calls_any_download_helper() -> None:
    """★ 第十二刀的形状断言：`run_job` 里**零** `download_*` 调用点（它们全在落地函数里）。

    这条比「名字还在不在」更硬：可以把调用点写成 `download.download_payload(...)` 或
    另抄一段内联逻辑而不碰任何名字表，AST 级的「不许有调用点」两种都挡。
    """
    for node in _tree(WORKER_FILE).body:
        if not isinstance(node, ast.FunctionDef) or node.name != "run_job":
            continue
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
    assert called, "没找到 run_job"
    stray = sorted(
        n for n in called if n.startswith("download_") or n in {"_cache_blob", "_resolve_blob"}
    )
    assert stray == [], f"run_job 里还有下载/缓存的调用点：{stray}——物料落地应当在 _ensure_* 里"
    for name in LANDING_NAMES:
        assert name in called, f"run_job 没有调 {name}"


def test_landing_call_sites_pass_every_parameter() -> None:
    """★ 接口双向一致：`run_job` 对落地函数的**每个**形参都传了（漏一个就红）。

    这是本刀最可能的失误形态（13 个形参跨两个调用）。位置实参按序对齐 `signature.parameters`，
    关键字参数按名对齐；两边合起来必须**恰好**等于形参集合——多传（打错关键字名）也红。
    """
    import inspect

    sigs = {
        name: inspect.signature(getattr(download_mod, name))
        for name in ("_ensure_payload", "_ensure_code")
    }
    seen: set[str] = set()
    for node in _tree(WORKER_FILE).body:
        if not isinstance(node, ast.FunctionDef) or node.name != "run_job":
            continue
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id not in sigs:
                continue
            func = call.func.id
            seen.add(func)
            params = list(sigs[func].parameters)
            positional = [
                p
                for p in params
                if sigs[func].parameters[p].kind is not inspect.Parameter.KEYWORD_ONLY
            ]
            passed_pos = [a for a in call.args if not isinstance(a, ast.Starred)]
            passed_kw = {k.arg for k in call.keywords if k.arg}
            assert len(passed_pos) <= len(positional), f"{func} 位置实参过多"
            covered = set(positional[: len(passed_pos)]) | passed_kw
            assert covered == set(params), (
                f"{func} 的形参没被完整覆盖：漏了 {sorted(set(params) - covered)}；"
                f"多传/打错名 {sorted(covered - set(params))}"
            )
    assert seen == {"_ensure_payload", "_ensure_code"}, f"run_job 没调全落地函数：{seen}"


def test_payload_and_code_downloads_resolve_in_this_module(monkeypatch, tmp_path: Path) -> None:
    """★ 功能性（本刀最关键的一条）：两个落地函数取字节时读的是**本模块**的下载函数。

    这是「patch 打偏」的正面警报：把炸弹放 `remote.worker.download_*`、真值放本模块，
    落地必须成功走到「sha 不匹配 ⇒ RetryableError」那一步（拿非 payload 字节，
    校验不过就停在那，不写任何文件）。
    """
    wrong = b"not-the-real-thing"
    called: list[str] = []

    def _boom(*_a, **_k):
        raise AssertionError("patch 打偏到了 remote.worker 命名空间")

    def _record(name):
        def _fn(*_a, **_k):
            called.append(name)
            return wrong

        return _fn

    monkeypatch.setattr(worker_mod, "download_payload", _boom)
    monkeypatch.setattr(worker_mod, "download_code", _boom)
    monkeypatch.setattr(download_mod, "download_payload", _record("payload"))
    monkeypatch.setattr(download_mod, "download_code", _record("code"))

    manifest = {"payload_sha256": "0" * 64, "code_sha256": "1" * 64}
    with pytest.raises(RetryableError):
        download_mod._ensure_payload(
            "http://hub", "t", "j1", manifest, tmp_path, preloaded=None, log=lambda _m: None
        )
    with pytest.raises(RetryableError):
        download_mod._ensure_code(
            "http://hub",
            "t",
            "j1",
            manifest,
            tmp_path / "j1",
            tmp_path,
            code_cache_root=None,
            preloaded=None,
            log=lambda _m: None,
        )
    assert called == ["payload", "code"], called
    # 没留下任何半截目录（sha 校验在写盘之前）
    assert not (tmp_path / "j1" / "code.zip").exists()


def test_progress_logger_still_has_exactly_two_production_copies() -> None:
    """`_progress_logger` 的孪生是**有意**的（tailscale_boot 独立拉取）⇒ 全仓恰好两份。

    先撤一道**廉价子串预筛**（`read_text`，缓存，~0.05ms/文件）再解析：判据是「这个文件里
    定义了 `_progress_logger`」，而函数名本身就是源码里的字面量 ⇒ 不含该子串的文件
    **不可能**定义它，预筛不丢判据。原来对 653 个文件全量 `ast.parse`（冷启动 ~2.7s，
    大头是 GC 在遍历刚建出来的 AST —— 见 `tests/helpers/source_scan.py` 的 2026-09-29 改判）。
    """
    found = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if ".venv" not in path.parts
        and "__pycache__" not in path.parts
        and "_progress_logger" in source_scan.read_text(str(path))
        and "_progress_logger" in _defined(path)
    )
    assert found == ["remote/download.py", "remote/tailscale_boot.py"], found


# ─────────────── 第十三刀：staged 快路径（plan/prefetch-stage-unpack §2.2/§2.6） ───────────────
#
# 预取线程把 payload 解好、`take()` 只交路径，**搬入**在这里（`_ensure_payload`）做唯一一次
# （晚于 `run_job` 的 `_result.json` 复用检查 —— 评审 P0-3）。三条判据：staged 命中不下载
# 不解包、暂存不在时**就地回退**关键下载、`payload_nbytes` 与字节路径的 `len()` 同口径。


def _packed(root: Path, *, shards: int = 1, tag: str = "a") -> bytes:
    """造一份**真**归档（tar.xz）：shard 目录带 `manifest.json` + 根级 `init_weights.json`。"""
    from common.payload import pack_payload

    src = root / f"src_{tag}"
    dirs: list[Path] = []
    for i in range(shards):
        d = src / f"w{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
        (d / "ep.bin").write_text(tag, encoding="utf-8")
        dirs.append(d)
    init = src / "init_weights.json"
    init.write_text(json.dumps({"tag": tag}), encoding="utf-8")
    out = src / "payload.tar.xz"
    pack_payload([str(d) for d in dirs], {"it": 1}, out, extra_files=[init])
    return out.read_bytes()


def test_staged_payload_is_adopted_without_download_or_unpack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """staged 命中：**不下载、不写 payload、不解包**，只把已解好的那份搬进 `job_dir`。"""
    staged = tmp_path / "prefetch" / "j1" / "shards"
    (staged / "w0").mkdir(parents=True)
    (staged / "w0" / "manifest.json").write_text("{}", encoding="utf-8")
    (staged / "init_weights.json").write_text("{}", encoding="utf-8")

    def _boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("staged 路径不该再下载/解包")

    monkeypatch.setattr(download_mod, "download_payload", _boom)
    monkeypatch.setattr(download_mod, "unpack_payload_or_fail", _boom)
    landed = download_mod._ensure_payload(
        "http://hub",
        "t",
        "j1",
        {"payload_sha256": "x" * 64},
        tmp_path,
        preloaded={"staged_dir": str(staged), "payload_nbytes": 4242, "unpack_sec": 3.3},
        log=lambda _m: None,
    )
    assert landed.staged is True
    assert landed.payload_nbytes == 4242 and landed.dl_sec == 0.0 and landed.unpack_sec == 3.3
    assert landed.job_dir == tmp_path / "j1"
    assert (landed.job_dir / "init_weights.json").exists(), "根级文件必须随搬入一起到位"
    assert not (landed.job_dir / "payload.tar.xz").exists(), "staged 路径不写 payload"
    assert landed.shard_dirs == [str(landed.job_dir / "w0")]


def test_staged_payload_missing_falls_back_to_the_critical_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """暂存不在（被清/prune 掉了）⇒ 一行日志 + **就地回退**关键下载（预取不是任务）。"""
    packed = _packed(tmp_path, tag="fb")
    monkeypatch.setattr(download_mod, "download_payload", lambda *a, **k: packed)
    logs: list[str] = []
    landed = download_mod._ensure_payload(
        "http://hub",
        "t",
        "j2",
        {"payload_sha256": hashlib.sha256(packed).hexdigest()},
        tmp_path,
        preloaded={
            "staged_dir": str(tmp_path / "gone" / "shards"),
            "payload_nbytes": 1,
            "unpack_sec": 1.0,
        },
        log=logs.append,
    )
    assert landed.staged is False and landed.payload_nbytes == len(packed)
    assert (landed.job_dir / "payload.tar.xz").exists(), "回退后必须走字节路径（写 payload + 解包）"
    assert any("回退关键下载" in m for m in logs), logs


def test_payload_nbytes_equals_the_byte_length_on_the_byte_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """字节路径的 `payload_nbytes` == `len(raw)`（改名不许改口径；worker 的 wire 账读它）。"""
    packed = _packed(tmp_path, tag="nb")
    monkeypatch.setattr(download_mod, "download_payload", lambda *a, **k: packed)
    landed = download_mod._ensure_payload(
        "http://hub",
        "t",
        "j3",
        {"payload_sha256": hashlib.sha256(packed).hexdigest()},
        tmp_path,
        preloaded=None,
        log=lambda _m: None,
    )
    assert landed.staged is False and landed.payload_nbytes == len(packed)


def test_scan_shard_dirs_matches_the_unpacker(tmp_path: Path) -> None:
    """`_scan_shard_dirs` 与 `common.payload.unpack_payload` 的 shard 判别同口径（等价性对照）。

    两处扫盘（解包器一次、staged 搬入后一次）必须给出同一份名单——否则 D14/装载会在
    staged 路径上看到另一组 shard。
    """
    from common.payload import unpack_payload

    packed = _packed(tmp_path, shards=2, tag="eq")
    zpath = tmp_path / "eq.tar.xz"
    zpath.write_bytes(packed)
    dest = tmp_path / "unpacked"
    dest.mkdir()
    _manifest, shard_dirs = unpack_payload(zpath, dest)
    scanned = download_mod._scan_shard_dirs(dest)
    assert [Path(p).name for p in scanned] == [Path(p).name for p in shard_dirs]
    assert len(scanned) == 2, "对照没测到东西（shard 名单为空）"

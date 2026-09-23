"""发布端自检：reader 全链试解（§4.5）+ 旧判别反向探测（§4.6），2026-09-21。

背景（plan/accident.plan.md §4）：C-0 it58 的 tar.xz **完好无缺**（3501788 字节、xz magic、
3476 个成员），却 `zipfile.is_zipfile()` 返回 True —— stdlib 的 EOCD 形似字节启发式误报 ⇒
旧 worker 把它送进 zip 解压 ⇒ `BadZipFile` ⇒ 每 5 分钟复现、零告警 3.5 小时。

本文件钉四件事：
  ① §4.5 自检**走 reader 全链**（`unpack_payload` 本体），并与打包输入**逐名对账**；
     产物不符/解不开 ⇒ 不通过（且 `.selfcheck` 目录不留残置）；
  ② §4.6 反向探测**故意**用旧启发式（`zipfile.is_zipfile`）：新 reader 能解 ≠ 旧 worker 能解；
     本文件用**真字节**夹具证明两者可以分叉（tar.xz + 尾随 zip 字节：旧判 True、新 reader 正常解）；
  ③ 不通过 ⇒ **显式扰动重打**（不扰动就逐字节相同，旧判在同一份字节上永远为真、重打闭环不收敛）；
  ④ 重打**有次数上限**（`PAYLOAD_SELFCHECK_ATTEMPTS`），触顶**响亮放弃**（`ProtocolError`，
     不发布），而不是无限重打。
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import PAYLOAD_NAME, PAYLOAD_PERTURB_NAME, ProtocolError
from remote import hub_client

# ------------------------------------------------------------------ 夹具


def _write_shard(root: Path, name: str = "rl_s0_seed1") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text('{"stage": 0}', encoding="utf-8")
    (d / "x.npy").write_bytes(b"\x93NUMPY" + b"\x00" * 32)
    return d


def _zip_bytes() -> bytes:
    """一段**真** zip 字节（用作尾随尾巴：验证时它是被 `is_zipfile` 认出来的那个东西）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("tail.txt", b"tail")
    return buf.getvalue()


def _append_zip_tail(path: Path) -> None:
    """把 zip 字节**接在** tar.xz 之后。

    这正是事故的字节形状：tar.xz 依旧完好（tarfile 按块读到归档结束标记，尾随垃圾不影响），
    而 `zipfile.is_zipfile` 会在文件尾 64KB 里找到 EOCD 签名并认下它（`_EndRecData` 只校验
    注释长度自洽，**不**验中央目录）——现场 3501788 字节那份是自然命中，本夹具是人工构造。
    """
    path.write_bytes(path.read_bytes() + _zip_bytes())


def _hidden_dir_members(path: Path) -> list[str]:
    with tarfile.open(path, "r:*") as tf:
        return tf.getnames()


# ------------------------------------------------------------------ ② 反向探测：真字节分叉


def test_appended_zip_tail_really_misjudges_old_heuristic_but_not_reader(tmp_path: Path) -> None:
    """真夹具：旧判为真（旧 worker 会 BadZipFile），而新 reader 解得好好的。"""
    shard = _write_shard(tmp_path)
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    hub_client.pack_payload([shard], {}, payload)
    _append_zip_tail(payload)

    assert hub_client._old_heuristic_misjudges(payload) is True, (
        "夹具失效：尾随 zip 字节没让旧判别误报（那本用例就证明不了分叉）"
    )
    ok, why = hub_client._payload_reader_selfcheck(payload, [shard], [])
    assert ok is True, why  # 新 reader 走「先 tar 后 zip」，这份字节一切正常
    assert _hidden_dir_members(payload)  # 归档结构确实还在


def test_probe_still_uses_the_old_heuristic(tmp_path: Path) -> None:
    """探测**故意**调旧启发式：真 zip ⇒ 真；正常 tar.xz ⇒ 假（不是恒真/恒假的桩）。"""
    real_zip = tmp_path / "legacy.zip"
    with zipfile.ZipFile(real_zip, "w") as z:
        z.writestr("a.txt", b"a")
    assert hub_client._old_heuristic_misjudges(real_zip) is True

    shard = _write_shard(tmp_path)
    plain = tmp_path / "plain.tar.xz"
    hub_client.pack_payload([shard], {}, plain)
    assert hub_client._old_heuristic_misjudges(plain) is False


# ------------------------------------------------------------------ ① reader 全链自检


def test_good_payload_passes_and_is_left_unperturbed(tmp_path: Path) -> None:
    shard = _write_shard(tmp_path)
    extra = tmp_path / "init_weights.json"
    extra.write_text("{}", encoding="utf-8")
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    msgs: list[str] = []

    sha = hub_client.pack_payload_zip([shard], [extra], payload, {}, log=msgs.append)

    assert sha == hashlib.sha256(payload.read_bytes()).hexdigest(), "返回的 sha 必须是落盘字节的"
    names = _hidden_dir_members(payload)
    assert PAYLOAD_PERTURB_NAME not in names, "一次通过就不该扰动（字节与历史口径一致）"
    assert extra.name in names
    assert msgs == [], f"一次通过不该有任何自检日志：{msgs}"
    assert not (payload.parent / ".selfcheck").exists(), "试解目录必须清干净"
    assert hub_client._payload_reader_selfcheck(payload, [shard], [extra]) == (True, "")


def test_selfcheck_rejects_content_mismatch_and_garbage(tmp_path: Path) -> None:
    a = _write_shard(tmp_path, "rl_s0_seed1")
    b = _write_shard(tmp_path, "rl_s0_seed2")
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    hub_client.pack_payload([a], {}, payload)

    # 打包输入比归档内容多一个 shard ⇒ 产物对不上（不是「能不能打开」的问题）
    ok, why = hub_client._payload_reader_selfcheck(payload, [a, b], [])
    assert ok is False and "不符" in why and b.name in why, why

    # 根本不是容器 ⇒ reader 全链试解失败
    bad = tmp_path / "job2" / PAYLOAD_NAME
    bad.parent.mkdir()
    bad.write_bytes(b"definitely-not-a-container")
    ok2, why2 = hub_client._payload_reader_selfcheck(bad, [], [])
    assert ok2 is False and "reader 全链试解失败" in why2, why2

    # 归档里少了本该带的额外文件也要抓到（存在的文件才算「本该带」——打包器就是按 is_file 决定）
    extra = tmp_path / "init_weights.json"
    extra.write_text("{}", encoding="utf-8")
    ok3, why3 = hub_client._payload_reader_selfcheck(payload, [a], [extra])
    assert ok3 is False and "缺额外文件" in why3, why3
    assert not (payload.parent / ".selfcheck").exists(), "失败路径同样要清掉试解目录"


# ------------------------------------------------------------------ ③/④ 重打与次数上限


def _stub_packer(
    real, tmp_path: Path, *, misjudge_first: bool, always_misjudge: bool, garbage_first: bool = False
):
    """替身打包器：可按需产出「会被旧判别误报的字节」/「坏字节」，其余交回真打包器。

    替身只为**造字节**，被验证的两个判据（探测 + reader 自检）全程都是真的——它们正是
    本文件的被测对象，不能一起被替身掉。
    """
    calls: list[bytes | None] = []

    def pack(shard_dirs, manifest, out_path, *, extra_files=None, perturb=None):
        calls.append(perturb)
        first = len(calls) == 1
        out = Path(out_path)
        if first and garbage_first:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"garbage-no-container")
            return hashlib.sha256(out.read_bytes()).hexdigest()
        sha = real(
            shard_dirs, manifest, out_path, extra_files=list(extra_files or []), perturb=perturb
        )
        if (first and misjudge_first) or always_misjudge:
            _append_zip_tail(out)
            sha = hashlib.sha256(out.read_bytes()).hexdigest()
        return sha

    return pack, calls


def test_misjudged_payload_triggers_perturbed_repack(tmp_path: Path, monkeypatch) -> None:
    """第 1 次命中旧判别 ⇒ 显式扰动重打 ⇒ 第 2 次字节确已扰动且两项检查都过。"""
    shard = _write_shard(tmp_path)
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    real = hub_client.pack_payload
    pack, calls = _stub_packer(real, tmp_path, misjudge_first=True, always_misjudge=False)
    monkeypatch.setattr(hub_client, "pack_payload", pack)
    msgs: list[str] = []

    sha = hub_client.pack_payload_zip([shard], [], payload, {}, log=msgs.append)

    assert len(calls) == 2, f"应恰好重打一次，实得 {len(calls)} 次"
    assert calls == [None, b"repack-2"], f"重打必须**显式扰动**字节：{calls}"
    assert sha == hashlib.sha256(payload.read_bytes()).hexdigest()
    names = _hidden_dir_members(payload)
    assert PAYLOAD_PERTURB_NAME in names, "重打后的归档里必须留下扰动标记（可审计）"
    assert hub_client._old_heuristic_misjudges(payload) is False, "重打后旧判别必须为假"
    assert hub_client._payload_reader_selfcheck(payload, [shard], []) == (True, "")
    assert any("第 1/3 次" in m and "误判" in m for m in msgs), msgs
    assert any("第 2 次通过" in m for m in msgs), msgs


def test_reader_selfcheck_failure_also_repacks(tmp_path: Path, monkeypatch) -> None:
    """§4.5 一侧的不通过（坏字节）走同一条重打路——不是只有旧判别才触发重打。"""
    shard = _write_shard(tmp_path)
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    pack, calls = _stub_packer(
        hub_client.pack_payload, tmp_path, misjudge_first=False, always_misjudge=False, garbage_first=True
    )
    monkeypatch.setattr(hub_client, "pack_payload", pack)
    msgs: list[str] = []

    sha = hub_client.pack_payload_zip([shard], [], payload, {}, log=msgs.append)

    assert len(calls) == 2
    assert sha == hashlib.sha256(payload.read_bytes()).hexdigest()
    assert hub_client._payload_reader_selfcheck(payload, [shard], []) == (True, "")
    assert any("reader 全链试解失败" in m for m in msgs), msgs


def test_attempt_cap_gives_up_loudly_with_distinct_bytes(tmp_path: Path, monkeypatch) -> None:
    """扰动了也仍旧被误判 ⇒ 触顶**响亮放弃**（不发布），且每次重打的字节确实不同。"""
    shard = _write_shard(tmp_path)
    payload = tmp_path / "job" / PAYLOAD_NAME
    payload.parent.mkdir()
    pack, calls = _stub_packer(
        hub_client.pack_payload, tmp_path, misjudge_first=False, always_misjudge=True
    )
    monkeypatch.setattr(hub_client, "pack_payload", pack)
    msgs: list[str] = []

    with pytest.raises(ProtocolError) as ei:
        hub_client.pack_payload_zip([shard], [], payload, {}, log=msgs.append)

    n = hub_client.PAYLOAD_SELFCHECK_ATTEMPTS
    assert n == 3, "上限变了就同步改本用例的措辞（这里是「三次」的语义钉子）"
    assert len(calls) == n, f"必须恰好重打 {n} 次（不许无限重打），实得 {len(calls)}"
    assert calls[0] is None and all(c is not None for c in calls[1:]), calls
    msg = str(ei.value)
    assert "放弃发布" in msg and f"连续 {n} 次不通过" in msg, msg
    assert any("第 2/3 次" in m for m in msgs), msgs  # 中间那次也要有行

"""remote/bundle.py —— **全离线**训练任务包（hub 导出 → Kaggle/Colab 上传 → 云机自主跑完）。

与「半离线」（`kind="run"`，hub 还在线发 job、只是不再逐轮决策）的差别只有一条：
**任务本身变成一个可搬动的文件**。hub 侧一条命令把整段任务打成 zip：

    python -m remote.bundle export --job-root <hub job 目录> --out tmp/task.zip ...

云机侧（Kaggle / Colab 会话里）两条命令：

    python -m remote.bundle import tmp/task.zip --dest /kaggle/working/battle2-<run>
    python -m remote.run_loop --artifacts /kaggle/working/battle2-<run> --device cuda

包里有「跑完整段所需的一切」：`plan.json`（跑哪些局、argv 模板、到哪停）、`manifest.json`
（课程 + 超参 + 血缘）、`init_weights.json`（起点权重）、`opt.tar`（Adam 动量，缺它续训静默归零）、
`code.zip`（**同 commit 的代码**——云机上是另一个 checkout，甚至没有仓：没有它 `build_pairs`
的重放就不成立）、`ts_code.zip`（rollout 导出器的 TS 运行时树）。`task.json` 是索引：逐件
sha256 + 字节数，导入时**逐件对账**（包要过 Kaggle dataset / Drive / 手工下载三道搬运，静默
截断是常态）。

为什么用「一个 zip + 两条命令」而不是「几条 curl + 一堆环境变量」：Kaggle 官方的任务入口是
**数据集/notebook 输出**，Colab 官方入口是 **Drive / 文件上传**——都是「搬文件」，不是「配网络」。
本模块只负责把文件摆成对方能理解的样子，网络那部分（若中途能连上 hub 就自动补传产物）在
`remote/uploader.py`，与包本身解耦：**包不依赖 hub 的在线状态**（导出时 hub 不可达也能导出）。

安全：`import_bundle` 的解压**不许越界**（zip-slip：绝对路径 / `..` / 盘符一律拒收）——这个 zip
的来源是「人手工搬来的文件」，是最典型的不可信输入。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

from remote.protocol import (
    BLOB_OPT,
    PLAN_NAME,
    ProtocolError,
    blob_path,
    decode_opt_tar,
    normalize_manifest,
)

#: 包索引名（zip 根）。
BUNDLE_INDEX = "task.json"
#: 包身份标记（防「拿错 zip」这类最贵的错误）。
BUNDLE_MAGIC = "battle2-task-bundle"
BUNDLE_PROTO = 1
#: 包内固定件名（与产物目录布局一致：导入后就是可直接续跑的产物目录）。
CODE_NAME = "code.zip"
TS_TREE_NAME = "ts_code"
COURSE_NAME = "course.jsonc"
README_NAME = "README.md"

#: 包内「数据件」（其余是索引/说明）：逐件都要 sha 对账。
DATA_PARTS = (PLAN_NAME, "manifest.json", "init_weights.json", COURSE_NAME, CODE_NAME)
OPTIONAL_PARTS = ("opt.tar", "ts_code.zip")


def sha256_bytes(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str | Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ 导出（hub 侧）


def export_bundle(
    out_zip: str | Path,
    *,
    manifest: dict,
    plan_bytes: bytes,
    init_weights_path: str | Path,
    code_zip_path: str | Path,
    job_dir: str | Path | None = None,
    ts_code_zip_path: str | Path | None = None,
    opt_tar_path: str | Path | None = None,
    hub_url: str = "",
    note: str = "",
) -> dict:
    """把一段任务打成 zip，返回索引（`task.json` 的内容）。

    `job_dir` 非空时从中取 `code.zip` / `ts_code.zip` / opt blob（`publish_job` 已经把这三样
    按内容寻址摆好了）——调用方因此不需要自己找文件。`hub_url` **只记地址不记 token**：
    token 是凭据，走 Kaggle secret / `--hub-token-file`，不进这个会被四处搬运的 zip。
    """
    m = normalize_manifest(manifest)
    if str(m.get("kind")) != "run":
        raise ProtocolError(f"任务包的 manifest 必须是 kind='run'，收到 {m.get('kind')!r}")
    plan_raw = bytes(plan_bytes)
    if sha256_bytes(plan_raw) != str(m.get("plan_sha256", "") or ""):
        raise ProtocolError(
            "plan_bytes 的 sha256 与 manifest.plan_sha256 不符——包与 job 不是同一份计划"
        )
    jd = Path(job_dir) if job_dir else None
    code_p = Path(code_zip_path)
    if not code_p.exists() and jd is not None:
        code_p = jd / CODE_NAME
    if not code_p.exists():
        raise ProtocolError(f"缺 code.zip（云机靠它拿到同 commit 的代码）: {code_p}")
    ts_p: Path | None = Path(ts_code_zip_path) if ts_code_zip_path else None
    if ts_p is None and jd is not None:
        cand = jd / "ts_code.zip"
        ts_p = cand if cand.exists() else None
    if ts_p is None or not ts_p.exists():
        raise ProtocolError(
            "缺 ts_code.zip（云机靠它跑 rollout：导出器是 TS）——先经 _ensure_ts_code 打包"
        )
    init_p = Path(init_weights_path)
    if not init_p.exists():
        raise ProtocolError(f"缺起点权重: {init_p}")
    opt_raw = b""
    if opt_tar_path and Path(opt_tar_path).exists():
        opt_raw = Path(opt_tar_path).read_bytes()
    elif jd is not None and blob_path(jd, BLOB_OPT).exists():
        opt_raw = blob_path(jd, BLOB_OPT).read_bytes()
    elif str(m.get("opt_init", "") or ""):
        try:
            opt_raw = decode_opt_tar(str(m["opt_init"]))
        except Exception:  # 内联 opt 坏掉不值得让导出失败（代价 = 云上 Adam 从头）
            opt_raw = b""

    parts: dict[str, bytes] = {
        PLAN_NAME: plan_raw,
        "manifest.json": json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8"),
        "init_weights.json": init_p.read_bytes(),
        CODE_NAME: code_p.read_bytes(),
        "ts_code.zip": ts_p.read_bytes(),
    }
    course_text = str(m.get("course", "") or "")
    if course_text:
        parts[COURSE_NAME] = course_text.encode("utf-8")
    if opt_raw:
        parts["opt.tar"] = opt_raw

    index = {
        "magic": BUNDLE_MAGIC,
        "proto": BUNDLE_PROTO,
        "run_id": str(m.get("runId", "") or ""),
        "it": int(m.get("it", 0) or 0),
        "end_it": int(json.loads(plan_raw.decode("utf-8")).get("end_it", 0)),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hub_url": str(hub_url or ""),
        "note": str(note or ""),
        "plan_sha256": str(m.get("plan_sha256", "")),
        "code_sha256": str(m.get("code_sha256", "")),
        "ts_code_sha256": str(m.get("ts_code_sha256", "")),
        "commit": str(m.get("commit", "")),
        "parts": {k: {"sha256": sha256_bytes(v), "bytes": len(v)} for k, v in parts.items()},
    }
    out = Path(out_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(BUNDLE_INDEX, json.dumps(index, ensure_ascii=False, indent=1))
        z.writestr(README_NAME, _readme(index))
        for name, raw in parts.items():
            z.writestr(name, raw)
    tmp.replace(out)
    return index


def _readme(index: dict) -> str:
    """包内说明（给人看的操作步骤——Kaggle / Colab 官方入口各一条）。"""
    return f"""battle2 全离线训练任务包
==========================

run_id      : {index['run_id']}    计划区间 : it{index['it']} → it{index['end_it']}
代码 commit : {index['commit']}
生成时间    : {index['created_at']}

包里是什么（task.json 逐件记 sha256，导入时对账）
-------------------------------------------------
  plan.json         本段要跑的每一轮：对集 + argv 模板 + 终点（`rl/plan.py`）
  manifest.json     课程全文 + 超参 + 血缘（D13/D14）
  init_weights.json 起点权重（= it{index['it']} 的输入）
  opt.tar           Adam 动量（**续训必需**；缺失 = 动量静默归零）
  code.zip          同 commit 的 python + TS 源码（云机不必有仓、不必联网）
  ts_code.zip       rollout 导出器的 TS 运行时树
  course.jsonc      课程文件快照（审计 / 热加载）

怎么上传（官方入口，二选一）
----------------------------
  Kaggle：把本 zip 作为**私有数据集**上传（或放进 notebook 的输入），会话里：
      !unzip -o /kaggle/input/<dataset>/task-*.zip -d /kaggle/working/battle2-task
      %cd <battle2 仓库（或带 code.zip 的任意目录）>
      !python -m remote.bundle import /kaggle/working/battle2-task --dest /kaggle/working/battle2-{index['run_id']}
  Colab：把 zip 拖进文件面板（或挂 Drive），会话里：
      !python -m remote.bundle import /content/task-*.zip --dest /content/drive/MyDrive/battle2-{index['run_id']}

怎么跑（云机自主，hub 不需要在线）
-----------------------------------
  python -m remote.run_loop --artifacts <上面的 --dest> --device cuda [--budget-sec 3600]

产物在哪 / 怎么下载
-------------------
  逐轮权重与指标都写进 <--dest>：
    it-NNN/weights.json（每轮权重）+ opt.tar（每轮动量）+ metrics.jsonl（每轮指标）
    state.json（续跑点）+ LATEST.zip（最新一轮小包）+ artifacts.zip（收尾全量包）
  Kaggle：目录就在 /kaggle/working 下 → Save Version 即打包下载（官方 output）。
  Colab ：落在 Drive 时直接可见；否则 `from google.colab import files; files.download(...)`。

中途能连上 hub 怎么办（产物补传）
-----------------------------------
  给个地址 + token 就会自动补传（地址包里的 hub_url 会自动带上；**token 不进包**）：
      python -m remote.run_loop --artifacts <上面的 --dest>
          先不用加 --hub-url：包里的 hub_url（{index['hub_url'] or '本包没记'}）会自动带上；
          没记就显式给 --hub-url <hub 地址>；token 用 --hub-token-file <只有本机能读到的文件>
      # 或用环境变量：export BATTLE_HUB_TOKEN=<token>（Kaggle secret / Colab 变量）
  云机每轮**落盘之后**尽力而为把该轮的权重与指标补传到 hub（连不上就静默跳过、下轮再试；
  第一次连上时会把之前攒的积压一次补齐，已投过的靠目录里的 delivered.json 不再重传）。
  产物目录里掉了一堆东西？无所谓：补传只是**第二份拷贝**——训练和交付从不等它。
"""


# ------------------------------------------------------------------ 导入（云机侧）


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> list[str]:
    """解压到 `dest`，**拒绝任何越界成员**（zip-slip：绝对路径 / `..` / 盘符）。

    这个 zip 的来源是「人从 dataset / Drive / 聊天窗口搬来的文件」，是最典型的不可信输入；
    一个 `../../.ssh/authorized_keys` 成员就能在云机上写任意文件。
    """
    dest = dest.resolve()
    names: list[str] = []
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        if raw.startswith("/") or ":" in raw.split("/")[0]:
            raise ProtocolError(f"包内成员是绝对路径（拒收）: {info.filename}")
        target = (dest / raw).resolve()
        if not str(target).startswith(str(dest)):
            raise ProtocolError(f"包内成员越界（zip-slip，拒收）: {info.filename}")
        names.append(raw)
    zf.extractall(dest)
    return names


def read_bundle_index(zip_path: str | Path) -> dict:
    """读包索引（不落盘）——导入前先看清楚这是哪个 run 的哪一段。"""
    p = Path(zip_path)
    if not p.exists():
        raise ProtocolError(f"任务包不存在: {p}")
    try:
        with zipfile.ZipFile(p) as zf:
            idx = json.loads(zf.read(BUNDLE_INDEX).decode("utf-8"))
    except KeyError as e:
        raise ProtocolError(f"不是任务包（缺 {BUNDLE_INDEX}）: {p}") from e
    except (ValueError, zipfile.BadZipFile) as e:
        raise ProtocolError(f"任务包损坏（不是合法 zip/json）: {p}: {e}") from e
    if not isinstance(idx, dict) or idx.get("magic") != BUNDLE_MAGIC:
        raise ProtocolError(f"任务包身份不符（magic != {BUNDLE_MAGIC}）: {p}")
    if int(idx.get("proto", -1)) != BUNDLE_PROTO:
        raise ProtocolError(f"任务包 proto={idx.get('proto')!r} != {BUNDLE_PROTO}")
    return idx


def import_bundle(zip_path: str | Path, dest: str | Path) -> dict:
    """把任务包铺成**可直接续跑的产物目录**，返回句柄 dict。

    布局与 `remote/artifacts.py` 的约定完全一致（`plan.json` + `manifest.json` + `it-NNN/`），
    所以导入之后 `python -m remote.run_loop --artifacts <dest>` 就能接着跑——不需要 hub、
    不需要仓、不需要装依赖（`code.zip` 与 `ts_code.zip` 都在包里）。
    """
    idx = read_bundle_index(zip_path)
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = _safe_extract(zf, root)
    parts = idx.get("parts") or {}
    for name in (*DATA_PARTS, *OPTIONAL_PARTS):
        want = parts.get(name)
        f = root / name
        if want is None:
            if name in DATA_PARTS:
                raise ProtocolError(f"任务包缺件: {name}")
            continue
        if not f.exists():
            raise ProtocolError(f"任务包索引声明了 {name}，解压后却不在: {f}")
        raw = f.read_bytes()
        if sha256_bytes(raw) != want.get("sha256") or len(raw) != int(want.get("bytes", -1)):
            raise ProtocolError(
                f"{name} 与索引不符（搬运截断/损坏？）——拒收，不跑任何一局"
            )
    # 起点 checkpoint：`it-{it}` = 本段的输入（= 上一段/上一条腿的产物）。
    # 位置与 `ArtifactStore.dir_for` 同规；`run_standalone` 从 state.json 接不上时也认它。
    start_it = int(idx.get("it", 0) or 0)
    it_dir = root / f"it-{start_it:03d}"
    it_dir.mkdir(parents=True, exist_ok=True)
    init_p = root / "init_weights.json"
    if not (it_dir / "weights.json").exists():
        shutil.copyfile(init_p, it_dir / "weights.json")
    opt_src = root / "opt.tar"
    if opt_src.exists() and not (it_dir / "opt.tar").exists():
        shutil.copyfile(opt_src, it_dir / "opt.tar")
    # TS 运行时树：解成 `ts_code/`（`run_standalone` 的 `ensure_ts_cache_layout` 认这个目录），
    # 同时保留 zip 原件——`run_job` 的 preloaded 直接吃 zip 字节（省一次解压重打）。
    ts_zip = root / "ts_code.zip"
    tree = root / TS_TREE_NAME
    if ts_zip.exists() and not tree.is_dir():
        tree.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(ts_zip) as zf:
                _safe_extract(zf, tree)
        except zipfile.BadZipFile as e:
            # 没有可用的运行时 = 跑不了一局：宁可现在响亮拒收，别等跑到 rollout 才炸。
            raise ProtocolError(
                f"ts_code.zip 不是合法 zip（包损坏/搬运截断？）——云机无法跑 rollout: {e}"
            ) from e
    with open(root / BUNDLE_INDEX, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    return {
        "artifacts_dir": str(root),
        "run_id": idx.get("run_id", ""),
        "it": start_it,
        "end_it": int(idx.get("end_it", 0) or 0),
        "plan": str(root / PLAN_NAME),
        "manifest": str(root / "manifest.json"),
        "init_weights": str(init_p),
        "code_zip": str(root / CODE_NAME),
        "ts_code_zip": str(ts_zip) if ts_zip.exists() else "",
        "ts_code_tree": str(tree) if tree.is_dir() else "",
        "hub_url": str(idx.get("hub_url", "") or ""),
        "parts": sorted(set(names)),
    }


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    """`python -m remote.bundle import <zip> --dest <dir>`：云机侧唯一的入口。

    导出侧不在这里：任务的 manifest 必须由**训练侧**（课程/超参/血缘的解析者）生成，
    拿一个不含 args 的 CLI 去重造它等于造第二份真相——导出是 trainer 的 `--export-bundle`。
    """
    ap = argparse.ArgumentParser(description="battle2 全离线任务包（导入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="把任务包铺成可直接续跑的产物目录")
    imp.add_argument("zip", help="任务包 zip")
    imp.add_argument("--dest", required=True, help="产物目录（就地续跑）")
    args = ap.parse_args(argv)

    try:
        idx = read_bundle_index(args.zip)
        print(
            f"[bundle] {idx['run_id']} it{idx['it']} → it{idx['end_it']}"
            f"（commit {str(idx.get('commit', ''))[:12]}，{idx['created_at']}）",
            flush=True,
        )
        got = import_bundle(args.zip, args.dest)
    except ProtocolError as e:
        print(f"[bundle] 导入失败：{e}", file=sys.stderr, flush=True)
        return 1
    print(
        f"[bundle] 已就位：{got['artifacts_dir']}\n"
        f"         下一步： python -m remote.run_loop --artifacts {got['artifacts_dir']}"
        + (f" --hub-url {got['hub_url']}" if got["hub_url"] else ""),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""remote/colab_bc.py — Colab BC 训练驱动（God-AI 蒸馏）。

notebook cell 只留「配置 + Drive + clone + 调本模块」；语料解压 / 训练启动 /
monitor / resume 全在这里。与 battle-bc.ipynb 的多 cell 版本等价，但可被
单 cell notebook 一行调用。

用法（repo clone 后）:
    python -m remote.colab_bc --run-tag p3bc --course p3-bc \
        --corpus-zip p3-godai.zip --epochs 60 --repo /content/battle2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [bc] {msg}", flush=True)


def sh(args: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(args, **kw)


def count_shards(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for s in d.iterdir() if s.is_dir() and (s / "obs.npy").exists())


# ── Drive ──────────────────────────────────────────────────────────


def mount_drive(drive_dir: Path) -> None:
    try:
        from google.colab import drive  # type: ignore

        drive.mount("/content/drive")
    except Exception as e:
        log(f"Drive 挂载失败（进度只在本机）: {e}")
    drive_dir.mkdir(parents=True, exist_ok=True)
    log(f"Drive: {drive_dir}")


# ── Python / bun ───────────────────────────────────────────────────


def find_python(nn: Path) -> str:
    venv_py = nn / ".venv" / "bin" / "python"
    if venv_py.exists():
        try:
            if sh([str(venv_py), "-c", "import torch,numpy"], check=False).returncode == 0:
                log(f"venv: {venv_py}")
                return str(venv_py)
        except Exception:
            pass
    sys_py = shutil.which("python3") or shutil.which("python") or "python3"
    try:
        if sh([sys_py, "-c", "import torch,numpy"], check=False).returncode == 0:
            log(f"系统 python: {sys_py}")
            return sys_py
    except Exception:
        pass
    log("bootstrap …")
    sh([sys_py, str(nn / "bootstrap.py")], cwd=str(nn))
    return str(venv_py)


def detect_device(py: str) -> str:
    r = sh([py, "-c", "import torch;print(torch.cuda.is_available())"], check=False)
    return "cuda" if r.stdout.strip() == "True" else "cpu"


def find_bun() -> str:
    for cand in (os.path.expanduser("~/.bun/bin/bun"), shutil.which("bun")):
        if cand and os.path.exists(cand):
            return cand
    log("安装 bun …")
    sh(["bash", "-lc", "curl -fsSL https://bun.sh/install | bash"])
    return os.path.expanduser("~/.bun/bin/bun")


# ── 语料 ───────────────────────────────────────────────────────────


def ensure_corpus(
    corpus: Path,
    corpus_zip: str,
    drive_dir: Path,
    mount_drive: bool,
    min_shards: int = 1000,
) -> int:
    n = count_shards(corpus)
    log(f"现有 shards: {n}")
    if n >= min_shards:
        return n

    zips: list[Path] = []
    if mount_drive:
        zips.append(drive_dir / corpus_zip)
    zips.append(corpus.parent.parent / "tmp" / corpus_zip)
    src = next((z for z in zips if z.exists()), None)
    if src is None:
        log(f"请上传语料 zip: {corpus_zip}")
        from google.colab import files  # type: ignore

        up = files.upload()
        fname = next(iter(up))
        dest = (drive_dir if mount_drive else corpus.parent.parent / "tmp") / corpus_zip
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(up[fname])
        src = dest
        log(f"已保存: {src} ({src.stat().st_size} bytes)")

    tmp = corpus.parent / (f".{corpus.name}-extract")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    log("解压中 …")
    with zipfile.ZipFile(src) as z:
        z.extractall(tmp)
    tops = list(tmp.iterdir())
    payload = None
    if len(tops) == 1 and tops[0].is_dir() and count_shards(tops[0]) >= min_shards:
        payload = tops[0]
    elif count_shards(tmp) >= min_shards:
        payload = tmp
    assert payload is not None, "zip 里找不到 shard"
    corpus.parent.mkdir(parents=True, exist_ok=True)
    if corpus.exists():
        shutil.rmtree(corpus)
    os.rename(str(payload), str(corpus))
    shutil.rmtree(tmp, ignore_errors=True)
    n = count_shards(corpus)
    log(f"corpus OK: {n} shards -> {corpus}")
    return n


# ── 训练 ───────────────────────────────────────────────────────────


def load_state(state_f: Path, total: int) -> dict:
    if state_f.exists():
        try:
            s = json.loads(state_f.read_text())
            # isinstance 收窄（json.loads 返回 Any）：非 dict 的 JSON（也被旧版的
            # `s["total"]` TypeError 挡掉）一律走下面的默认值，语义不变。
            if isinstance(s, dict):
                s["total"] = total
                return s
        except Exception:
            pass
    return {"total": total, "done": 0, "base": 0, "running": False, "pid": None}


def save_state(state_f: Path, s: dict) -> None:
    state_f.write_text(json.dumps(s, indent=1))


def latest_ckpt_n(run_dir: Path) -> int:
    best = 0
    for f in run_dir.glob("weights.json.ckpt.*"):
        m = re.search(r"\.ckpt\.(\d+)$", f.name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def bank_progress(run_dir: Path, state_f: Path, s: dict) -> dict:
    k = latest_ckpt_n(run_dir)
    if k > 0:
        eff = s["base"] + k
        if eff > s["done"]:
            s["done"] = eff
            src = run_dir / f"weights.json.ckpt.{k}"
            if src.exists():
                tmp = run_dir / ".resume_seed.tmp"
                shutil.copy(src, tmp)
                os.replace(tmp, run_dir / "resume_seed.json")
            save_state(state_f, s)
    return s


def run_training(
    py: str,
    nn: Path,
    corpus: Path,
    run_dir: Path,
    log_path: Path,
    state_f: Path,
    device: str,
    total_epochs: int,
    seed: int,
    tag: str,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    s = load_state(state_f, total_epochs)
    base = s.get("done", 0)
    remaining = s["total"] - base
    if remaining <= 0:
        log(f"已完成 {base}/{s['total']}")
        return

    for f in run_dir.glob("weights.json.ckpt.*"):
        f.unlink()

    args = [
        py, "-u", "train/bc.py",
        "--data-dir", str(corpus),
        "--out", str(run_dir / "weights.json"),
        "--arch", "student", "--value-coef", "0.5",
        "--epochs", str(remaining), "--ckpt-every", "1",
        "--batch", "256", "--lr", "0.003",
        "--val-split", "0.1", "--mirror-p", "0.5",
        "--num-workers", "0", "--seed", str(seed),
        "--device", device, "--epoch-offset", str(base),
        "--notes", f"colab-ts {tag} {base + 1}/{s['total']}",
    ]
    resume = run_dir / "resume_seed.json"
    if base > 0 and resume.exists():
        args += ["--resume", str(resume)]

    log(f"launch: {' '.join(args)}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(log_path, "ab")
    proc = subprocess.Popen(
        args, cwd=str(nn), stdout=lf, stderr=subprocess.STDOUT, start_new_session=True
    )
    s.update({"running": True, "pid": proc.pid, "base": base})
    save_state(state_f, s)
    log(f"pid={proc.pid} -> {s['total']} epochs (log: {log_path})")

    # monitor
    t0 = time.time()
    pos = 0
    last_print = 0
    while proc.poll() is None:
        s = bank_progress(run_dir, state_f, load_state(state_f, total_epochs))
        try:
            txt = log_path.read_text(errors="replace")
            for line in txt[pos:].splitlines():
                if "epoch" in line.lower() and "loss" in line:
                    print("[train]", line.strip()[:160])
            pos = len(txt)
        except Exception:
            pass
        el = int(time.time() - t0)
        if el - last_print >= 120:
            last_print = el
            log(f"{el}s | {s['done']}/{s['total']} epochs")
        time.sleep(20)

    rc = proc.wait()
    s = load_state(state_f, total_epochs)
    if rc == 0:
        s["done"] = s["total"]
    else:
        s = bank_progress(run_dir, state_f, s)
    s["running"] = False
    s["pid"] = None
    save_state(state_f, s)
    log(f"退出 rc={rc} | {s['done']}/{s['total']}")


# ── 入口 ───────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Colab BC 训练驱动")
    p.add_argument("--run-tag", required=True)
    p.add_argument("--course", required=True)
    p.add_argument("--corpus-zip", required=True)
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--repo", default="/content/battle2")
    p.add_argument("--min-shards", type=int, default=1000)
    p.add_argument("--no-drive", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo)
    nn = repo / "nn-training"
    mount = not args.no_drive
    drive_dir = Path("/content/drive/MyDrive") / f"battle2-{args.run_tag}"
    corpus = repo / "tmp" / f"{args.run_tag}-corpus"
    run_dir = drive_dir / "run"
    log_path = Path("/content") / f"{args.run_tag}-train.log"
    state_f = drive_dir / "run_state.json"

    # 依赖检查
    need = [nn / "train" / "bc.py", nn / "curricula" / f"{args.course}.jsonc"]
    missing = [str(x) for x in need if not x.exists()]
    if missing:
        log(f"FATAL: 缺少 {missing} —— 先推送到仓库")
        return 1

    if mount:
        mount_drive(drive_dir)

    py = find_python(nn)
    device = detect_device(py)
    log(f"DEVICE = {device}")
    ver = sh([py, "-c", "import sys,torch;print(sys.version.split()[0],'torch',torch.__version__)"]).stdout.strip()
    log(ver)

    ensure_corpus(corpus, args.corpus_zip, drive_dir, mount, args.min_shards)
    run_training(py, nn, corpus, run_dir, log_path, state_f, device, args.epochs, args.seed, args.run_tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())

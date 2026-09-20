"""measure_checkpoint_rss —— 单进程 N 课的 torch 栈真机 RSS 实测（R2c-3 余项）。

**为什么要它**：单进程 supervisor 要为 N 门课各持一份 torch 栈（model + Adam + 冻结 ref），
而 `rl.checkpointCacheCourses` / `rl.checkpointCacheMb` 的默认值此前**只能拍脑袋**——本机 0 卡、
remote 为主，没有真机数据（plan/r2-loop-task-queue §6 把「实测表」列为 R2c 上线前置条件）。
本脚本按训练路径**同一套构建代码**把栈造出来，逐课累加测 RSS，产出一张可复现的表。

**口径（与训练路径逐条对齐，差异都写在这里）**：

- 模型：训练路径用 `build_model`（可能触发 BC warm-start 子进程并把权重写回 `args.out`）。
  本脚本只重放它的**架构 + 权重装载**两步（`build_ppo` / `PPO.build_rl_net` +
  `load_state_into` / `load_*_weights`），**不写任何文件**——参数个数与字节占用与训练路径一致，
  而内存测量不需要 warm-start 这个副作用。
- **先暖一次再测**：torch 的惰性初始化（第一次 kernel 选择 / 分配器建池 / Adam 首次 step 的
  状态缓存）会让**第一份**栈看起来贵出两个量级（实测 78MB vs 真值 0.75MB）。故先造一份**丢弃**的
  栈把这块一次性开销吃掉，`torch 基线` 之后才开测；这一点不写清楚，表就会把人带偏。
- **栈必须活着**（`alive`）：测的是「supervisor 同时持有 N 课」的累计曲线，栈一旦被 gc 回收，
  第 2 课的增量会变成 0（分配器复用），曲线就是假的。
- Adam：`torch.optim.Adam` 第一次 `step()` 才分配 m/v。本脚本用一次**零梯度 step**
  把它们真正分配出来（数值无副作用）；梯度缓冲在真实训练里也一直驻留，故一并计入 `grads`。
- ref：`mode ∈ {intent, goal}` 且 `kickstart_kl > 0` ⇒ 一份冻结的 `args.out` 快照；
  `mode = per-tick` 且 `kickstart_ref` ⇒ 一份冻结的课程 bc 快照（与 `_ensure_local_ppo_stack` 同构）。
- **刻意不测**：PPO 一轮的 episodes/chunk 缓冲（`mb` 决定）是**另一处峰值**，已由 forensics
  埋点；它随轮次涨落、不是缓存上限要管的常驻量——混进来会让这张表既不可比也不可复现。

**读法**：关注「每课增量」列（torch 基线几百 MB 与课程数无关；每课增量才决定
`checkpointCacheMb` 该给多少）。

用法：
  python scripts/measure_checkpoint_rss.py --course c4-dodge --max-courses 5
  python scripts/measure_checkpoint_rss.py --modes per-tick,intent,goal --json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
NN_ROOT = HERE.parent
REPO_ROOT = NN_ROOT.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

CURRICULA = NN_ROOT / "curricula"
MODES = ("per-tick", "intent", "goal")


# ────────────────────────────── 纯函数（可脱 torch 单测） ──────────────────────────────


@dataclass
class StackRow:
    """一门课的 torch 栈占用（MB；由分阶段 RSS 差值得出）。"""

    course: str
    mode: str
    #: 实际装载的权重文件（空 = 默认架构）；**测的是哪份权重**是这张表的可信度前提。
    weights: str
    #: 被跳过的候选（schema 不符 / 装载失败）——报告里必须可见，否则读者以为测了线上权重。
    skipped: str
    params: int
    model_mb: float
    grads_mb: float
    adam_mb: float
    refs_mb: float
    rss_delta_mb: float

    @property
    def total_mb(self) -> float:
        return self.model_mb + self.grads_mb + self.adam_mb + self.refs_mb

    @property
    def theory_mb(self) -> float:
        """理论值：float32 × (权重 1 + 梯度 1 + Adam m/v 2 + refs 份数)。

        它的用途是**换算**：任何规模的真实课程都可以按
        `params × 4 × (2 + 2 + refs) / 1e6` 算出自己那一档，不必再跑一次本脚本。
        """
        refs = 1.0 if self.refs_mb > 0 else 0.0
        return self.params * 4 * (2 + 2 + refs) / 1e6


def recommend(cache_courses: int, per_course_mb: float, *, headroom: float = 1.5) -> tuple[int, str]:
    """由实测的「每课增量」推荐 `checkpointCacheMb`（向上取到 256MB 的整数倍）。

    字节硬顶是**数量上限之外的第二道保险**：只要每课增量远小于一个 256MB 块，真实约束就是
    `checkpointCacheCourses`（数量），字节顶几乎永不触发——这正是实测要回答的问题。
    """
    raw = max(1.0, per_course_mb) * max(1, cache_courses) * headroom
    mb = int(-(-raw // 256) * 256)  # ceil 到 256 的整数倍
    return mb, (
        f"{cache_courses} 课 × {per_course_mb:.2f}MB/课 × {headroom:.1f} 余量 ≈ {raw:.0f}MB"
        f" ⇒ checkpointCacheMb = {mb}"
    )


def render_table(
    rows: list[StackRow], *, baseline_mb: float, cum: list[float], cache_courses: int
) -> str:
    """人读表：每课一行（分阶段 MB）+ 累计 RSS 曲线 + 推荐值。"""
    out: list[str] = []
    hdr = (
        f"{'course':<22} {'mode':<9} {'params':>8} {'model':>7} {'grads':>7} {'adam':>7} "
        f"{'refs':>7} {'per-course':>10}"
    )
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows:
        out.append(
            f"{r.course:<22} {r.mode:<9} {r.params:>8} {r.model_mb:>7.2f} {r.grads_mb:>7.2f} "
            f"{r.adam_mb:>7.2f} {r.refs_mb:>7.2f} {r.total_mb:>10.2f}"
        )
        out.append(f"    weights: {r.weights or '（默认架构：无可用权重文件）'}")
        if r.skipped:
            out.append(f"    跳过候选: {r.skipped}")
        out.append(f"    理论值 {r.theory_mb:.2f}MB / 实测 {r.total_mb:.2f}MB")
    out.append("")
    out.append(f"torch 基线 RSS（已暖过一次、造任何测量的栈之前）：{baseline_mb:.1f}MB（与课程数无关）")
    for i, c in enumerate(cum, start=1):
        out.append(f"  N={i} 本进程累计 RSS：{c:.1f}MB")
    per = max((r.total_mb for r in rows), default=0.0)
    mb, text = recommend(cache_courses, per)
    out.append("")
    out.append(f"每课增量（取最大）：{per:.2f}MB ⇒ {text}")
    return "\n".join(out)


# ────────────────────────────── 测量（torch 懒导入） ──────────────────────────────


def _rss_mb_windows() -> float | None:
    """Windows 的当前 RSS：PSAPI `GetProcessMemoryInfo().WorkingSetSize`（零第三方依赖）。

    为什么不用 `resource`：那是 Unix-only 模块，Windows 上 `import resource` 直接
    ModuleNotFoundError（2026-09-20 实测：Windows 跑本脚本在 `rss_mb()` 当场崩）。
    """
    import ctypes
    from ctypes import wintypes

    # 对应 Win32 的 `PROCESS_MEMORY_COUNTERS`；类名按仓库 lint（pep8-naming N801）取 CapWords。
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # `ctypes.WinDLL` 只在 win32 的 typeshed 里声明 —— Linux 上 mypy 必报 attr-defined
    # （.github/workflows/nn-training.yml 的 `uv run mypy .` 跑在 Linux），而本函数只在
    # Windows 被调用（平台分派见上面 `rss_mb()`）。同款惯例见下方 os.sysconf 那行。
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)  # type: ignore[attr-defined]
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        pmc = ProcessMemoryCounters()
        pmc.cb = ctypes.sizeof(pmc)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return float(pmc.WorkingSetSize) / 1e6
    except Exception:
        return None


def rss_mb() -> float:
    """当前进程 RSS：Linux 走 `/proc/self/statm`，Windows 走 PSAPI，其余退 `ru_maxrss`。

    拿不到一律返回 0.0（不要抛）——本脚本的目的是出内存表，平台差异不该让它崩。
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        # os.sysconf is Unix-only; Windows stubs omit it (same as rl/forensics.py).
        page_size = int(os.sysconf("SC_PAGE_SIZE"))  # type: ignore[attr-defined]
        return float(pages * page_size) / 1e6
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    if sys.platform == "win32":
        v = _rss_mb_windows()
        return v if v is not None else 0.0
    try:
        import resource
    except ImportError:  # 非 Unix 且无 resource（兜底，别让平台差异炸掉整份报告）
        return 0.0
    v = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return v / 1024 if sys.platform != "darwin" else v / (1024 * 1024)


def build_net(mode: str, path: str) -> Any:
    """按 mode 造一张网并装载权重（与 `_ensure_local_ppo_stack` 的「造网 + 装权重」同一对调用）。

    `path` 为空 = 默认架构（不做权重装载）。
    """
    import ppo.engine as ppo_mod

    if mode in ("intent", "goal"):
        import ppo.goal as ppo_goal
        import ppo.intent as ppo_intent
        from rl.modes import get_backend

        model = get_backend(mode).build_rl_net(path or None)
        if path:  # 空路径 = 默认架构（不装载）——`load_*_weights("")` 会 FileNotFoundError
            (ppo_goal.load_goal_weights if mode == "goal" else ppo_intent.load_intent_weights)(
                model, path
            )
        return model
    model = ppo_mod.build_ppo(path or None)
    if path:
        from data.weights_io import load_state_into

        load_state_into(model, path)
    return model


def resolve_weights(course: str, explicit: str | None) -> list[str]:
    """权重候选（按优先级；**第一个能装载的**被用，其余记进报告）。

    顺序：`--rl-path` > 课程 `out` > 课程 `bc` > `weights/` 下新→旧的 json；末尾空串 =
    默认架构。为什么是候选列表而不是「取最新一个」：本机 `weights/` 里同时躺着 schema_major=2
    的旧权重（schema 红线：obs 布局变更后**不得**装载），取最新只会直接报错退出——那不是本脚本
    要报告的事（内存表不该被无关的旧文件卡住），但**跳过了哪些**必须在报告里可见。
    """
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else REPO_ROOT / p
        return [str(p), ""]
    out: list[str] = []
    try:
        from rl.config import load_course

        cfg = load_course(CURRICULA / f"{course}.jsonc")
        for rel in (cfg.out, cfg.bc):
            p = Path(rel)
            p = p if p.is_absolute() else REPO_ROOT / p
            if p.exists():
                out.append(str(p))
    except Exception:
        pass  # 课程配置缺失/非法 → 直接走 weights/ 兑底（用了哪个在报告里看得到）
    cands = sorted(
        (q for q in (NN_ROOT / "weights").rglob("*.json") if q.is_file()),
        key=lambda q: q.stat().st_mtime,
        reverse=True,
    )
    out.extend(str(q) for q in cands)
    out.append("")
    return out


def build_stack(
    label: str, candidates: list[str], mode: str, *, keep: list[Any] | None = None
) -> StackRow:
    """按训练路径的构建顺序造一门课的栈，分阶段读 RSS 差值。

    候选权重逐个试（第一个装得上的即用，失败的记进 `skipped`）；全失败 ⇒ 默认架构。
    `keep` 非空时把 torch 对象塞进去**保命**（见模块 docstring：栈被 gc 掉的话累计曲线是假的）。
    ★ 不写盘、不改课程文件（`build_model` 的 warm-start/落盘副作用刻意不复现）。
    """
    import torch

    r0 = rss_mb()
    rl_path = ""
    skipped: list[str] = []
    model = None
    for cand in candidates:
        try:
            model = build_net(mode, cand)
            rl_path = cand
            break
        except Exception as e:  # schema 不符 / 文件损坏 / 形状不匹配：换下一个候选
            if cand:
                skipped.append(f"{Path(cand).name}({type(e).__name__})")
    if model is None:
        model = build_net(mode, "")
    model.to(torch.device("cpu"))
    params = sum(p.numel() for p in model.parameters())
    r1 = rss_mb()

    # 梯度缓冲（真实训练里一直驻留）：零梯度不产生数值副作用
    for p in model.parameters():
        p.grad = torch.zeros_like(p)
    r2 = rss_mb()

    # Adam 的 m/v 只有在第一次 step() 时才分配
    trainable = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.Adam(trainable, lr=3e-4)
    opt.step()
    r3 = rss_mb()

    # 冻结 ref（与 `_ensure_local_ppo_stack` 同构：intent/goal 走 out 快照，per-tick 走课程 bc）
    ref = build_net(mode, rl_path) if rl_path or mode in ("intent", "goal") else None
    if mode == "per-tick":
        bc = _course_bc_path(label)
        ref = build_net(mode, bc) if bc else None
    if ref is not None:
        for p in ref.parameters():
            p.requires_grad = False
        ref.eval()
        ref.to(torch.device("cpu"))
    r4 = rss_mb()

    if keep is not None:
        keep.append((model, opt, ref))
    return StackRow(
        course=label,
        mode=mode,
        weights=rl_path,
        skipped=", ".join(skipped[:3]) + (" …" if len(skipped) > 3 else ""),
        params=params,
        model_mb=round(r1 - r0, 2),
        grads_mb=round(r2 - r1, 2),
        adam_mb=round(r3 - r2, 2),
        refs_mb=round(r4 - r3, 2),
        rss_delta_mb=round(r4 - r0, 2),
    )


def _course_bc_path(course: str) -> str:
    """课程 `kickstart_ref` 冻结快照的源文件（per-tick 缰绳）；不启用/文件缺失 → 空串。"""
    try:
        from rl.config import load_course

        cfg = load_course(CURRICULA / f"{course}.jsonc")
        if not bool(getattr(cfg, "kickstart_ref", False)):
            return ""
        bc = Path(cfg.bc)
        bc = bc if bc.is_absolute() else REPO_ROOT / bc
        return str(bc) if bc.exists() else ""
    except Exception:
        return ""


def _course_mode(course: str) -> str:
    try:
        from rl.config import load_course

        return str(load_course(CURRICULA / f"{course}.jsonc").mode)
    except Exception:
        return "per-tick"


def warm_up() -> float:
    """吃掉 torch 的一次性开销（惰性 kernel / 分配器建池 / Adam 首步缓存），返回暖后的基线。

    丢掉的这份栈**不进报告**：它的数字是「torch 冷启 + 一课」，与「N 课的增量」不可比。
    """
    build_stack("__warmup__", [""], "per-tick")
    gc.collect()
    return round(rss_mb(), 1)


# ────────────────────────────── CLI ──────────────────────────────


def _plan(args: argparse.Namespace) -> list[tuple[str, list[str], str]]:
    """要测的 (标签, 权重候选, mode) 列表。`--modes` 优先（各 mode 的默认架构）。"""
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if modes:
        bad = [m for m in modes if m not in MODES]
        if bad:
            raise SystemExit(f"[rss] --modes 含未知模式 {bad}（可用：{list(MODES)}）")
        return [(f"{m}:默认架构", [""], m) for m in modes]
    names = [c.strip() for c in args.courses.split(",") if c.strip()]
    if args.course:
        names = [args.course, *names]
    if not names:
        raise SystemExit("[rss] 需要 --course / --courses / --modes 之一")
    return [
        (c, resolve_weights(c, args.rl_path or None), _course_mode(c))
        for c in (names[i % len(names)] for i in range(max(1, args.max_courses)))
    ]


def main(argv: list[str] | None = None) -> int:
    from platform_utils import force_utf8_stdio

    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="单进程 N 课的 torch 栈 RSS 实测（R2c-3 余项）")
    ap.add_argument("--course", default="", help="单门课（可重复取不同课程混跑）")
    ap.add_argument("--courses", default="", help="逗号分隔的课程名（循环取用）")
    ap.add_argument("--modes", default="", help="逗号分隔：按各 mode 的默认架构测（per-tick,intent,goal）")
    ap.add_argument("--max-courses", type=int, default=5, help="模拟的并行课程数（默认 5）")
    ap.add_argument("--cache-courses", type=int, default=5, help="推荐值用的缓存课程数上限")
    ap.add_argument("--rl-path", default="", help="强制指定权重文件（默认按课程 out/bc 解析）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（文档/CI 消费）")
    ap.add_argument("--no-warmup", action="store_true", help="跳过暖机（调试用；数字会偏大）")
    args = ap.parse_args(argv)

    baseline = round(rss_mb(), 1) if args.no_warmup else warm_up()
    alive: list[Any] = []  # 保命引用：栈活着才是真实占用（见模块 docstring）
    rows: list[StackRow] = []
    cum: list[float] = []
    for label, cands, mode in _plan(args):
        rows.append(build_stack(label, cands, mode, keep=alive))
        cum.append(round(rss_mb(), 1))

    if args.json:
        per = max(r.total_mb for r in rows)
        mb, text = recommend(args.cache_courses, per)
        print(
            json.dumps(
                {
                    "baseline_mb": baseline,
                    "rows": [asdict(r) for r in rows],
                    "cum_mb": cum,
                    "per_course_mb": round(per, 2),
                    "recommended_cache_mb": mb,
                    "recommendation": text,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(render_table(rows, baseline_mb=baseline, cum=cum, cache_courses=args.cache_courses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

/** course-open-check.ts — 开课前的**课程配置体检**（2026-11-10 用户口径：
 *  「不要等 ppo 时才错误退出」）。
 *
 *  判据**不在这里重写**：课程文件是 pydantic 模型（`biz/course_spec.py`，`extra="forbid"`）、
 *  运行时链是 argparse + `apply_course` + `validate_args`（`worker/course_args.py::course_openable`）、
 *  权重清单是 `worker/data/weights_meta.py::read_weights_meta`。三者都在训练栈里，控制台只负责
 *  **在开课那一拍**把它们跑一遍并原文上屏。
 *
 *  为什么不在 TS 里重写一份课程 schema：课程文件字段几百个，pydantic 的 `extra="forbid"` 判据
 *  写两遍不可能同步；TS 侧多写一份就是第二个事实源（AGENTS §2.4 的反面）。这里只做「调用 +
 *  解析 + 措辞」，判据永远只有 python 那一份。
 *
 *  代价与边界：开课因此需要 venv 里能 import pydantic 的 python —— 这与「开课后要跑训练」是
 *  同一份环境；缺了它训练本来就起不来 ⇒ **响亮拒绝**（跳过 = 用户要消灭的那类迟到失败）。
 *  探测全程只读（`course_openable` 零副作用；权重侧只落地 delta 容器 / 解 JSON 清单，
 *  与 PPO 自己的消费前处理同源）。
 */

import { existsSync } from 'fs'
import path from 'path'
import { curriculaDir, NN_TRAINING, REPO_ROOT, tmpLogsDir } from '../core/paths'
import { resolveVenvPython } from '../core/venv'

/** 体检结论（`weights=null` = 这门课不消费基础权重，如 BC 课）。 */
export interface OpenCheckResult {
  ok: boolean
  /** 课程文件 / 运行时校验链的原文原因（`ok=true` 时为空串）。 */
  reason: string
  weights: { path: string; ok: boolean; reason: string } | null
}

/** 探测函数类型（开课动作的注入点：单测不拉起训练栈）。 */
export type OpenableProbe = (course: string, weightsPath: string | null) => OpenCheckResult

/** python 侧结果行的标记（stdout 可能混着 `log()` 的告警行，只认带标记的那行）。 */
const OUT_MARK = '__OPENCHECK__'

/**
 * 体检脚本（`python -c`）：一次进程里做完三件事，任一件失败都进结论。
 *
 *   ① 课程文件 schema（pydantic，`extra="forbid"`；`level` 引用、stages/player 冲突、
 *      未知键都在这里响亮）——**按 kind 分流**：BC 走 `load_bc_course`（`BcCourseConfig`，
 *      与 `trainer/run_bc.py` 同一条链），RL 走 `load_course`（`CourseConfig`）；
 *   ② RL 课再跑 `course_openable(stem)` —— 与真开课**同一条**运行时链（rl-config → argparse →
 *      `apply_course` → 冲突检测 → `validate_args`，含 kickstart/门禁/互斥/范围）；
 *   ③ 基础权重：delta 容器先按消费者口径落地（`ensure_standard_weights`），再
 *      `read_weights_meta` 校验 format / schema_major / params。
 *
 *  BC 课不做 ②（`course_openable` 的覆盖边界就是 BC 另一条链），也不校验权重（BC 不 warm-start）。
 */
const CHECK_SCRIPT = `
import json, os, sys

course_path, kind, wpath = sys.argv[1], sys.argv[2], sys.argv[3]
out = {"ok": True, "reason": "", "weights": None}

def fail(msg):
    out["ok"] = False
    out["reason"] = msg

try:
    if kind == "bc":
        # BC 课必须用 BcCourseConfig（与 trainer/run_bc.py 同一条校验链）；
        # 用 RL 的 CourseConfig 会把 kind/corpus/train/eval 全判成 extra_forbidden。
        from worker.bc_config import load_bc_course
        load_bc_course(course_path)
    else:
        from biz.course_resolve import load_course
        load_course(course_path)
except BaseException as e:
    fail(f"{type(e).__name__}: {e}")

stem = os.path.basename(course_path).replace(".bc.jsonc", "").replace(".jsonc", "")
if out["ok"] and kind != "bc":
    try:
        from worker.course_args import course_openable
        ok, reason = course_openable(stem)
        if not ok:
            fail(f"课程配置不可开课: {reason}")
    except BaseException as e:
        fail(f"{type(e).__name__}: {e}")

if out["ok"] and wpath:
    try:
        # 旧盘上可能是 int16 delta 容器：消费者本来就先落地再读（remote/weights_standard.py）。
        # 不落地就报「JSON 解析失败」会把「需要转换」误诊成「权重坏了」。
        try:
            from remote.weights_standard import ensure_standard_weights
            wpath = str(ensure_standard_weights(wpath))
        except BaseException:
            pass
        from worker.data.weights_meta import read_weights_meta
        read_weights_meta(wpath)
        out["weights"] = {"path": wpath, "ok": True, "reason": ""}
    except BaseException as e:
        out["weights"] = {"path": wpath, "ok": False, "reason": f"{type(e).__name__}: {e}"}

print(${JSON.stringify(OUT_MARK)} + json.dumps(out, ensure_ascii=False))
`

/** 课程文件绝对路径（`.bc.jsonc` 优先，与 `stack/courses.ts::isBcCourse` 同一判据）。
 *  调用方已用 `assertCourseExists` 保证存在；这里只给探测脚本一个确定的落点。 */
export function courseFilePath(course: string): string {
  const bc = path.join(curriculaDir(), `${course}.bc.jsonc`)
  return existsSync(bc) ? bc : path.join(curriculaDir(), `${course}.jsonc`)
}

/** 解析器：只认带标记的一行；没有标记 ⇒ 抛（python 没跑到 print 就退了，stderr 由调用方带出）。 */
export function parseOpenCheckOutput(stdout: string): OpenCheckResult {
  const line = stdout
    .split(/\r?\n/)
    .filter((l) => l.startsWith(OUT_MARK))
    .pop()
  if (!line) throw new Error('体检脚本没有输出结论行（python 可能在 import 阶段就退出了）')
  const raw = JSON.parse(line.slice(OUT_MARK.length)) as Record<string, unknown>
  if (typeof raw.ok !== 'boolean' || typeof raw.reason !== 'string') {
    throw new Error(`体检结论形状不对：${line.slice(OUT_MARK.length)}`)
  }
  const w = raw.weights as Record<string, unknown> | null | undefined
  const weights =
    w && typeof w === 'object' && typeof w.path === 'string' && typeof w.ok === 'boolean'
      ? { path: w.path, ok: w.ok, reason: String(w.reason ?? '') }
      : null
  return { ok: raw.ok, reason: raw.reason, weights }
}

/** python 解释器：venv 真实解释器 > `PYTHON` > PATH 上的 python3/python。
 *  找不到 ⇒ 抛（开课拒绝并点名怎么修——这条检查不能静默跳过）。 */
function pythonForCheck(): { python: string; sitePackages: string } {
  const venv = resolveVenvPython()
  if (existsSync(venv.python)) return venv
  const sys = process.env.PYTHON || Bun.which('python3') || Bun.which('python')
  if (sys) return { python: sys, sitePackages: venv.sitePackages }
  throw new Error(
    '找不到 python 解释器（nn-training/.venv 未就绪，PATH 上也没有 python3/python）——' +
      '开课前体检要在训练栈的 python 里跑课程 schema；先 `bun run setup` / 装好 venv 再开课。',
  )
}

/** 真探测：起一个短命 python 子进程跑 {@link CHECK_SCRIPT}，原文结论进 {@link OpenCheckResult}。
 *
 *  cwd = 仓根（与 `trainer/run_rl.py` 自己的 `os.chdir(仓根)` 同口径：课程文件里的相对路径
 *  按仓根解析）；`PYTHONPATH` 挂 venv site-packages（uv 跳板真身不认 venv 的包）。 */
export function runCourseOpenCheck(course: string, weightsPath: string | null): OpenCheckResult {
  const { python, sitePackages } = pythonForCheck()
  const args = [
    python,
    '-c',
    CHECK_SCRIPT,
    courseFilePath(course),
    existsSync(path.join(curriculaDir(), `${course}.bc.jsonc`)) ? 'bc' : 'rl',
    weightsPath ?? '',
  ]
  const r = Bun.spawnSync(args, {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      PYTHONPATH: [sitePackages, NN_TRAINING].filter(Boolean).join(path.delimiter),
    },
    stdout: 'pipe',
    stderr: 'pipe',
    windowsHide: true,
  })
  const stdout = r.stdout?.toString() ?? ''
  const stderr = r.stderr?.toString() ?? ''
  try {
    return parseOpenCheckOutput(stdout)
  } catch (e) {
    const tail = stderr.trim().split(/\r?\n/).slice(-4).join('\n')
    throw new Error(
      `${e instanceof Error ? e.message : String(e)}` +
        (tail ? `\nstderr（末几行）：\n${tail}` : ''),
    )
  }
}

/** 开课时要体检的那份**基础权重**：活动权重 > 封存起点 > 课程文件声明的 BC 种子。
 *
 *  第三种情形**不预判存在性**：缺文件时校验器会响亮报（而播种那一步同样会失败）——
 *  这里静默返回一个不存在的路径是有意的，错误信息由校验器带路径给出。 */
export function baseWeightsPath(
  course: string,
  seedPath: string | undefined,
  bcSeed: string,
): string {
  const active = path.join(tmpLogsDir(), course, 'weights.json')
  if (existsSync(active)) return active
  if (seedPath && existsSync(seedPath)) return seedPath
  return bcSeed
}

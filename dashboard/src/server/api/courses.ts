/** courses.ts — 课程发现与单一事实源（?course= 只读覆盖、effectiveCourse、动作上下文）。 */
import { existsSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT, curriculaDir, tmpLogsDir } from '../../core/paths'
import { type StartCtx, loadConsoleState } from '../actions'
import { PostBody, bodyStr } from './route'

// ────────────────── 视图课程参数校验（局域网只读防线 2：GET ?course= 只读覆盖） ──────────────────

/** 校验 GET 端点（/api/state /api/pool /api/log/<key> /log/<key>）的 ?course= 参数：
 *  只放行真实存在的课程（tmp/<course> 目录或 curricula/<course>.jsonc）且形态限
 *  [A-Za-z0-9._-]——杜绝路径穿越（?course=../xxx）与任意路径读取。非法/空 → 返回 ''，
 *  视图静默回退操作员课程（服务端不报错；LAN 只是多看了一眼）。 */
export function sanitizeViewCourse(raw: string | null): string {
  if (!raw) return ''
  if (!/^[A-Za-z0-9._-]+$/.test(raw)) return ''
  try {
    if (existsSync(path.join(REPO_ROOT, 'tmp', raw))) return raw
    if (existsSync(path.join(curriculaDir(), `${raw}.jsonc`))) return raw
    if (existsSync(path.join(curriculaDir(), `${raw}.bc.jsonc`))) return raw
  } catch {
    /* 回退自动课程 */
  }
  return ''
}

// ────────────────────────── 课程发现 ──────────────────────────

/** 发现可监控课程：tmp/ 下含 training_log.jsonl 的目录（按日志 mtime 新→旧）+ curricula/*.jsonc 中尚未落盘的课程。
 *
 *  窗口（max）默认取足量 500：课程目录随阶梯（+20）/经典（+35）/BC（*.bc.jsonc）持续
 *  增长，小窗口会把课程挤出课程 select——2026-09-14 回归：20 个 ladder-*（mtime 23:32）
 *  占满 12 窗口，bc-c4-v3（23:14）连 bc-c4/c6-chip 一并消失，无法在控制台开启 BC 训练。 */
export function discoverCourses(max = 500): string[] {
  const out: Array<{ name: string; mtime: number }> = []
  const seen = new Set<string>()
  try {
    for (const ent of readdirSync(tmpLogsDir(), { withFileTypes: true })) {
      if (!ent.isDirectory()) continue
      const lp = path.join(tmpLogsDir(), ent.name, 'training_log.jsonl')
      try {
        out.push({ name: ent.name, mtime: statSync(lp).mtimeMs })
        seen.add(ent.name)
      } catch {
        /* no log — not a course dir */
      }
    }
  } catch {
    return []
  }
  // 补充 curricula/ 中尚未跑过的课程（按 jsonc mtime 新→旧），使新 course 首次选择有 UI 路径
  try {
    for (const ent of readdirSync(curriculaDir(), {
      withFileTypes: true,
    })) {
      if (ent.isDirectory() || !ent.name.endsWith('.jsonc')) continue
      // BC 课程（<name>.bc.jsonc，2026-09-13）：课程键 = 去掉 .bc.jsonc 后缀
      const name = ent.name.endsWith('.bc.jsonc')
        ? ent.name.slice(0, -'.bc.jsonc'.length)
        : ent.name.replace(/\.jsonc$/, '')
      if (seen.has(name)) continue
      const cp = path.join(curriculaDir(), ent.name)
      out.push({ name, mtime: statSync(cp).mtimeMs })
    }
  } catch {
    /* no curricula dir — fall through */
  }
  return out
    .sort((a, b) => b.mtime - a.mtime)
    .slice(0, max)
    .map((c) => c.name)
}

// ───────────────────── 课程单一事实源（DECISIONS §351 bug 1） ─────────────────────

/** console-state 中课程字段的形态（loadConsoleState 的结构子集）。 */
export interface ConsoleCourseState {
  course: string
}

/** 生效课程：console-state 优先，空则回退最近活跃课程（与页面显示同源）。
 *  读路径不回写 console-state——保持「手动选择才持久化」语义。 */
export function effectiveCourse(state: ConsoleCourseState, discovered: string[]): string {
  return state.course || discovered[0] || ''
}

/** 动作上下文（routeAction 专用）：显式 body.course > effectiveCourse。
 *  保证「页面显示的课程 = 动作实际使用的课程」不变量。
 *
 *  ★ 2026-09-19：不再带 `trainerPpo`——启动训练不选模式（pull 由远端 worker 自己来领，
 *  push 由 rl-config 里登记的 push worker 节点决定）。 */
export function actionCtx(body: PostBody): StartCtx {
  const state = loadConsoleState()
  return {
    course: bodyStr(body, 'course') || effectiveCourse(state, discoverCourses()),
  }
}

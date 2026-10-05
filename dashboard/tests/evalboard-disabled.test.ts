/** evalboard-disabled.test.ts — evalBoard 停用（R4）的门控与接线守卫
 *
 * 分层：src/core/feature-flags.ts（开关）· src/server/eval-board/routes.ts（决策 + 计数器）
 *      · src/web/render.tsx（说明页）· src/server/server.ts（三处接线，源码守卫）
 *
 * 依据 plan/dashboard-memory-evalboard-off.plan.md（W1′–W4′ + §8 G2/G3/G12）：
 *  - 缺省关、`BCITY_EVALBOARD=1` 可开（**惰性函数**，env 注入 + 还原）；
 *  - 开关关 ⇒ `/api/evalboard` 不 compose（`composeEvalBoardView` 调用数 = 0）；
 *  - `/eval` 出说明页且**不注入 `/eval.js`**（R2-1：钉真实 bundle 名，「不含 eval-app」是假绿）；
 *  - 不起 30s ladder ticker（纯函数 + 源码守卫）；`server.ts` 零 `buildEvalBoardView(` 直调；
 *  - `games/**` 零写入（停用只读路径不碰账本）；
 *  - `src/web/**` 零 `process.env`（G12：进了客户端 bundle 就是浏览器 ReferenceError）。
 */
import { afterEach, describe, expect, it } from 'bun:test'
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  evalboardApiPayload,
  evalboardComposeCalls,
  evalboardPageDecision,
  evalboardPageNotice,
  evalboardRouteCounters,
  resetEvalboardComposeCalls,
  resetEvalboardRouteCounters,
  shouldStartLadderTicker,
} from '../src/server/eval-board'
import { evalboardEnabled } from '../src/core/feature-flags'
import { DASHBOARD_ROOT } from '../src/core/paths'

const FLAG = 'BCITY_EVALBOARD'
const DATA = 'EVALBOARD_DATA'
const savedEnv = new Map<string, string | undefined>()

function setEnv(key: string, value: string | undefined): void {
  if (!savedEnv.has(key)) savedEnv.set(key, process.env[key])
  if (value === undefined) delete process.env[key]
  else process.env[key] = value
}

function mkTempRoot(prefix: string): string {
  return mkdtempSync(path.join(tmpdir(), prefix))
}

/** 只取 `#root` 内的渲染体 —— 整个 theme.css 被内联进 `<style>`，对整页 `toContain` 会被
 *  样式表文本满足（web-ssr-eval-page.test.ts 同款教训）。 */
function body(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

afterEach(() => {
  for (const [k, v] of savedEnv) {
    if (v === undefined) delete process.env[k]
    else process.env[k] = v
  }
  savedEnv.clear()
  resetEvalboardRouteCounters()
  resetEvalboardComposeCalls()
})

describe('evalBoard 停用（R4）', () => {
  it('evalboard_disabled_by_default：不带 env ⇒ 关', () => {
    setEnv(FLAG, undefined)
    expect(evalboardEnabled()).toBe(false)
    expect(shouldStartLadderTicker()).toBe(false)
  })

  it('flag_on_when_env_set：BCITY_EVALBOARD=1 ⇒ 开（惰性读，无需动态 import）', () => {
    setEnv(FLAG, '1')
    expect(evalboardEnabled()).toBe(true)
    expect(shouldStartLadderTicker()).toBe(true)
  })

  it('endpoint_does_not_compose_when_disabled：disabled 形状 + 零 compose', () => {
    setEnv(FLAG, undefined)
    resetEvalboardComposeCalls()
    const payload = evalboardApiPayload('c4-margin', false)
    if (!('disabled' in payload)) throw new Error('开关关时必须返回 disabled 形状')
    expect(payload.disabled).toBe(true)
    expect(payload.message).toContain('BCITY_EVALBOARD')
    expect(payload.flag).toBe('BCITY_EVALBOARD')
    expect(payload.message).toBe(evalboardPageNotice()) // API 与说明页 = 同一文案来源
    expect(evalboardComposeCalls()).toBe(0)
    expect(evalboardRouteCounters.apiDisabled).toBe(1)
  })

  it('endpoint_composes_when_enabled：反向验证计数器能涨（否则上面那条恒真）', () => {
    setEnv(FLAG, '1')
    const root = mkTempRoot('evalboard-on-')
    try {
      setEnv(DATA, root)
      resetEvalboardComposeCalls()
      const view = evalboardApiPayload('__nope__', true)
      expect('disabled' in view).toBe(false)
      expect(evalboardComposeCalls()).toBe(1)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('eval_page_is_notice_when_disabled：说明页决策 + 零 compose + 文案含开关', async () => {
    setEnv(FLAG, undefined)
    resetEvalboardComposeCalls()
    const d = evalboardPageDecision(['c4-margin'])
    expect(d.kind).toBe('notice')
    if (d.kind !== 'notice') throw new Error('unreachable')
    expect(d.message).toContain('BCITY_EVALBOARD')
    expect(evalboardComposeCalls()).toBe(0)
    const { renderEvalNoticePage } = await import('../src/web/render')
    const html = renderEvalNoticePage(d.message)
    expect(html).toContain('评估页已停用')
    expect(html).toContain(d.message)
    // R2-1：钉真实 bundle 名。「不含 eval-app」恒真（该字符串从不出现）——不许那样写。
    expect(html).not.toContain('/eval.js')
    expect(html).not.toContain('<script src=')
  })

  it('eval_page_views_when_enabled：开关打开时取数分支照常（反向验证）', () => {
    setEnv(FLAG, '1')
    const root = mkTempRoot('evalboard-on-page-')
    try {
      setEnv(DATA, root)
      resetEvalboardComposeCalls()
      const d = evalboardPageDecision(['__nope_page__'])
      expect(d.kind).toBe('views')
      if (d.kind !== 'views') throw new Error('unreachable')
      expect(d.views.length).toBe(1)
      expect(evalboardComposeCalls()).toBe(1)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('homepage_omits_eval_summary_when_disabled：SSR 不含挂载点且不误伤 Hero', async () => {
    setEnv(FLAG, undefined)
    const { buildStateView } = await import('../src/server/api')
    const { renderConsolePage } = await import('../src/web/render')
    const state = await buildStateView('p4-fast')
    expect(state.evalboardEnabled).toBe(false)
    const dom = body(renderConsolePage(state))
    expect(dom).not.toContain('tc-eval-summary')
    expect(dom).toContain('tc-hero') // 停用只删这一块：RL 区其余读数照旧
  })

  it('homepage_keeps_eval_summary_when_enabled：开关打开 ⇒ 挂载点回来', async () => {
    setEnv(FLAG, '1')
    const { buildStateView } = await import('../src/server/api')
    const { renderConsolePage } = await import('../src/web/render')
    const state = await buildStateView('p4-fast')
    expect(state.evalboardEnabled).toBe(true)
    expect(body(renderConsolePage(state))).toContain('tc-eval-summary')
  })

  it('homepage_without_evalboard_keeps_state_fields：非 eval 字段逐字段相等（G-C）', async () => {
    const { buildStateView } = await import('../src/server/api')
    setEnv(FLAG, undefined)
    const off = (await buildStateView('p4-fast')) as unknown as Record<string, unknown>
    setEnv(FLAG, '1')
    const on = (await buildStateView('p4-fast')) as unknown as Record<string, unknown>
    expect(Object.keys(on).sort()).toEqual(Object.keys(off).sort())
    // time = 墙钟；evalboardEnabled = 本刀本身。其余字段必须逐字不变（DR-plan G7 同口径）。
    const skip = new Set(['time', 'evalboardEnabled'])
    const diffs = Object.keys(off).filter(
      (k) => !skip.has(k) && JSON.stringify(on[k]) !== JSON.stringify(off[k]),
    )
    expect(diffs).toEqual([])
  })

  it('disabled_evalboard_does_not_touch_data_dir：停用路径不写 games/**', () => {
    setEnv(FLAG, undefined)
    const root = mkTempRoot('evalboard-off-')
    try {
      const games = path.join(root, 'games')
      mkdirSync(games, { recursive: true })
      const shard = path.join(games, 'cA.jsonl')
      writeFileSync(shard, `${JSON.stringify({ course: 'cA', seed: 1 })}\n`)
      writeFileSync(path.join(root, 'runner_state.json'), '{}')
      const before = statSync(shard)
      setEnv(DATA, root)
      evalboardApiPayload('cA', true)
      const d = evalboardPageDecision(['cA'])
      expect(d.kind).toBe('notice')
      const after = statSync(shard)
      expect(after.mtimeMs).toBe(before.mtimeMs)
      expect(after.size).toBe(before.size)
      expect(readdirSync(games)).toEqual(['cA.jsonl'])
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})

// ────────────────────────── 源码守卫（结构性回归闸） ──────────────────────────

const SERVER_SRC = path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts')

function walkFiles(dir: string): string[] {
  const out: string[] = []
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) out.push(...walkFiles(p))
    else if (/\.(ts|tsx)$/.test(ent.name)) out.push(p)
  }
  return out
}

describe('evalBoard 停用：源码守卫', () => {
  it('server.ts 零 buildEvalBoardView( 直调，三处接线全经 routes', () => {
    const src = readFileSync(SERVER_SRC, 'utf-8')
    expect(src.includes('buildEvalBoardView(')).toBe(false)
    expect(src).toContain('evalboardApiPayload(')
    expect(src).toContain('evalboardPageDecision(')
    expect(src).toContain('renderEvalNoticePage(')
  })

  it('ladderTimer 受 shouldStartLadderTicker() 门控（删接线 c 必红）', () => {
    const src = readFileSync(SERVER_SRC, 'utf-8')
    expect(src).toMatch(
      /if \(shouldStartLadderTicker\(\)\) \{\s*\n\s*evalboardRouteCounters\.ladderStarted \+= 1\s*\n\s*const ladderTimer = setInterval\(/,
    )
  })

  it('src/web/** 零 process.env（G12：进了 bundle 就是浏览器 ReferenceError）', () => {
    const offenders = walkFiles(path.join(DASHBOARD_ROOT, 'src', 'web')).filter((f) =>
      readFileSync(f, 'utf-8').includes('process.env'),
    )
    expect(offenders).toEqual([])
  })
})

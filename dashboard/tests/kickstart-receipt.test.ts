/**
 * kickstart-receipt.test.ts — 开课回执的「起点-基线对照行」（plan/accident.plan.md §5.3）。
 *
 *  事故：C 双臂从收敛权重（it175）以 `kk(1)=1` 满额缰绳复活，it1 kl=0.90 连烧 30 轮，
 *  两臂 ×12h ≈ 一整天算力换来「无结论」。开课前盘上就已经有的那个事实——「本腿恢复的
 *  权重已经在课程 bc 权重那一档，还要拿满额锚去拉」——没人被告知。本文件钉四件事：
 *
 *   ① **同源**：读数只有一套账本（`tmp/<课>/eval_log.jsonl`），取法与 python
 *      `kickstart_burn.baseline_reading` / `loop_lifecycle._kickstart_baseline_row` 一致
 *      （后者 S4 第十九刀前住 `loop_core`）
 *      （it0 = 基线；文件序末条 it>0 = 起点），并与控制台既有的 `readEvalSummaries` 对账；
 *   ② **镜像常量不许漂**：噪声带 / 点数 / 响亮阈值对着 python 源码核对（改了 python 就红）；
 *   ③ **判据的分支全在纯函数里**：低于基线 / 噪声带内 / 高于噪声带 / 缰绳关 / 缺读数；
 *   ④ **观测永不阻断开课**：账本缺失/坏行只是少一行字，不抛。
 *
 *  环境重定向：课程目录与 traj 根指向临时目录（`core/paths.ts` 惰性取值）——不读也不写线上
 *  任何一份账本/课程文件。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT } from '../src/core/paths'
import type { RlConfig } from '../src/core/types'
import {
  BURN_MARGIN_PP,
  BURN_POINTS,
  KICKSTART_DEFAULT_WARN,
  burnThresholds,
  composeBaselineLines,
  kickstartKnobs,
  kickstartReceipt,
  readLedger,
} from '../src/stack/kickstart-receipt'
import { readEvalSummaries } from '../src/server/iters'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-kk-receipt-'))
const CUR = path.join(DIR, 'curricula')
const TRAJ = path.join(DIR, 'traj')
process.env.BCITY_CURRICULA_DIR = CUR
process.env.BCITY_TMP_LOGS_DIR = TRAJ

beforeAll(() => {
  mkdirSync(CUR, { recursive: true })
  mkdirSync(TRAJ, { recursive: true })
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

/** 写一门课（课程目录下的 jsonc；`kickstart_init` 缺省不写 = 缺席）。 */
function writeCourse(name: string, body: Record<string, unknown>): void {
  writeFileSync(path.join(CUR, `${name}.jsonc`), JSON.stringify(body, null, 2), 'utf-8')
}

/** 一行 eval_summary（字段与真账本同名同形）。 */
function summary(iter: number, winRate: number, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ event: 'eval_summary', iter, winRate, games: 400, ...extra })
}

/** 一行逐局 eval（体积占账本 99% 的那种）。 */
function game(iter: number): string {
  return JSON.stringify({ event: 'eval', iter, stage: 2000, seed: 860011, win: 0, kills: 2 })
}

function writeLedger(course: string, lines: string[]): string {
  const dir = path.join(TRAJ, course)
  mkdirSync(dir, { recursive: true })
  const p = path.join(dir, 'eval_log.jsonl')
  writeFileSync(p, `${lines.join('\n')}\n`, 'utf-8')
  return p
}

// ────────────────────────── ① 镜像常量（对着 python 源码核对） ──────────────────────────

describe('镜像常量（权威在 python；这里防漂）', () => {
  const burnPy = readFileSync(path.join(REPO_ROOT, 'nn-training/rl/kickstart_burn.py'), 'utf-8')

  /**
   * 按**定义**在 `nn-training/rl/` 源码树里找（不写死文件）：按写死路径读源码的守卫会在下一
   * 次搬家时静默失效或假红（S16 事故）。2026-09-25（S4 第十九刀）`KICKSTART_DEFAULT_WARN`
   * 随主循环骨架从 `loop_core.py` 搬到 `loop_lifecycle.py`，本函数就是那次修的形态。
   */
  function rlSourceDefining(name: string): string {
    const dir = path.join(REPO_ROOT, 'nn-training/rl')
    for (const f of readdirSync(dir)) {
      if (!f.endsWith('.py')) continue
      const src = readFileSync(path.join(dir, f), 'utf-8')
      if (new RegExp(`^${name}\\s*=`, 'm').test(src)) return src
    }
    throw new Error(`${name} 在 nn-training/rl/ 里找不到（改名了？同步本文件与镜像常量）`)
  }

  /** 取 `NAME = <数字>` 的字面量（源码级核对：不 import python，只读文本）。 */
  function literal(src: string, name: string): number {
    const m = src.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, 'm'))
    expect(m, `${name} 未在 python 源码里找到（改名了？同步本文件与镜像常量）`).not.toBeNull()
    return Number(m![1])
  }

  it('噪声带 / 连续点数 = `rl/kickstart_burn.py` 的常量', () => {
    expect(BURN_MARGIN_PP).toBe(literal(burnPy, 'BURN_MARGIN_PP'))
    expect(BURN_POINTS).toBe(literal(burnPy, 'BURN_POINTS'))
  })

  it('响亮阈值 = `rl/loop_lifecycle.py::KICKSTART_DEFAULT_WARN`（S4 第十九刀起）', () => {
    expect(KICKSTART_DEFAULT_WARN).toBe(
      literal(rlSourceDefining('KICKSTART_DEFAULT_WARN'), 'KICKSTART_DEFAULT_WARN'),
    )
  })

  it('账本筛选键与 python `read_trend_rows` 同一字面量（event=eval_summary）', () => {
    const gatePy = readFileSync(path.join(REPO_ROOT, 'nn-training/rl/gate_check.py'), 'utf-8')
    expect(gatePy).toContain('r.get("event") != "eval_summary"')
  })
})

// ────────────────────────── ② 账本读数（起点 / 基线） ──────────────────────────

describe('readLedger：起点 = 文件序末条 it>0；基线 = 末条 it0（同 python）', () => {
  it('正常账本：it0 两条（两腿各补派一次基线）取末条，it>0 取末条', () => {
    const p = writeLedger('kk-a', [
      summary(0, 0.35),
      game(0),
      summary(1, 0.3),
      game(1),
      summary(57, 0.355),
      summary(0, 0.36), // 第二腿重跑基线：末条 it0 = 0.36
      summary(58, 0.34),
    ])
    const r = readLedger(p)
    expect(r.exists).toBe(true)
    expect(r.base).toBe(0.36)
    expect(r.baseRows).toBe(2)
    expect(r.last).toBe(0.34)
    expect(r.lastIter).toBe(58)
  })

  it('坏行 / 非数值读数 / 非 summary 行一律跳过（半截行 = 进程被杀在写中途）', () => {
    const p = writeLedger('kk-bad', [
      summary(0, 0.2),
      '{"event": "eval_summary", "iter": 3, "winRate": ', // 半截行
      JSON.stringify({ event: 'eval_summary', iter: 4, winRate: null }),
      JSON.stringify({ event: 'eval_summary', iter: '5', winRate: 0.9 }),
      JSON.stringify({ event: 'run_start', iter: 6, winRate: 0.99 }),
      '',
    ])
    const r = readLedger(p)
    expect(r.base).toBe(0.2)
    expect(r.last).toBeNull()
    expect(r.lastIter).toBeNull()
  })

  it('文件不存在 = 合法状态（新课程/新腿），不是错误', () => {
    const r = readLedger(path.join(TRAJ, 'kk-missing', 'eval_log.jsonl'))
    expect(r).toEqual({ exists: false, base: null, baseRows: 0, last: null, lastIter: null })
  })

  it('与既有的 `readEvalSummaries` 对账（同一份账本两个读法必须同数）', () => {
    const p = writeLedger('kk-xcheck', [
      summary(0, 0.125),
      summary(1, 0.13),
      summary(175, 0.145),
      summary(176, 0.141),
    ])
    const light = readLedger(p)
    const full = readEvalSummaries(path.join(TRAJ, 'kk-xcheck'))
    expect(light.base).toBe(full.get(0)!.winRate!)
    const iters = [...full.keys()].filter((i) => i > 0)
    const maxIter = Math.max(...iters)
    expect(light.last).toBe(full.get(maxIter)!.winRate!)
    expect(light.lastIter).toBe(maxIter)
  })
})

// ────────────────────────── ③ 旋钮与阈值 ──────────────────────────

describe('kickstartKnobs：初值来源必须说清（C 事故的病灶）', () => {
  it('课程显式 kickstart_init ⇒ 来源写课程', () => {
    writeCourse('kk-explicit', { kickstart_ref: true, kickstart_init: 0.3 })
    const k = kickstartKnobs('kk-explicit', null)
    expect(k).toEqual({
      ref: true,
      init: 0.3,
      source: '课程 kickstart_init=0.3',
      contradiction: false,
    })
  })

  it('课程未写 ⇒ 回落 rl-config `rl.kickstart_kl`，再回落 CLI 缺省 1.0', () => {
    writeCourse('kk-default', { kickstart_ref: true })
    const withCfg = kickstartKnobs('kk-default', {
      version: 1,
      nodes: [],
      rl: { hub_port: 1, agent_port: 2, remote_token: 't', kickstart_kl: 0.25 },
    } as RlConfig)
    expect(withCfg.init).toBe(0.25)
    expect(withCfg.source).toBe('rl-config rl.kickstart_kl=0.25 —— 课程未写 kickstart_init')

    const bare = kickstartKnobs('kk-default', null)
    expect(bare.init).toBe(1.0)
    expect(bare.source).toContain('缺省 1.0')
  })

  it('声明了初值却没开缰绳 ⇒ contradiction（python `apply_course` 启动期拒启）', () => {
    writeCourse('kk-contra', { kickstart_init: 1 })
    expect(kickstartKnobs('kk-contra', null).contradiction).toBe(true)
    // 显式 0 放行（python 侧同规：0 = 关闭，不是矛盾）
    writeCourse('kk-zero', { kickstart_init: 0 })
    expect(kickstartKnobs('kk-zero', null).contradiction).toBe(false)
  })

  it('课程文件缺失/读不了 ⇒ 全部按缺省（观测不阻断，不抛）', () => {
    const k = kickstartKnobs('no-such-course', null)
    expect(k.ref).toBe(false)
    expect(k.init).toBe(1.0)
  })
})

describe('burnThresholds：阈值走 rl-config `courses.<课>.kickstart_burn`（与 python 同键）', () => {
  const cfg = {
    version: 1,
    nodes: [],
    rl: { hub_port: 1, agent_port: 2, remote_token: 't' },
    courses: { 'kk-thr': { kickstart_burn: { margin_pp: 8, points: 2 } } },
  } as RlConfig

  it('有覆盖用覆盖，缺席用 python 常量镜像', () => {
    expect(burnThresholds('kk-thr', cfg)).toEqual({ marginPp: 8, points: 2 })
    expect(burnThresholds('kk-none', cfg)).toEqual({
      marginPp: BURN_MARGIN_PP,
      points: BURN_POINTS,
    })
  })
})

// ────────────────────────── ④ 对照行（纯函数分支） ──────────────────────────

describe('composeBaselineLines：C 例本该被拦下来问一句', () => {
  const burn = { marginPp: 5.0, points: 3 }
  const logPath = 'tmp/x20-clutch/eval_log.jsonl'
  const kk = (over: Partial<ReturnType<typeof kickstartKnobs>> = {}) => ({
    ref: true,
    init: 1.0,
    source: '缺省 1.0 —— 课程未写 kickstart_init、rl-config 未设 rl.kickstart_kl',
    contradiction: false,
    ...over,
  })
  const rd = (
    over: Partial<ReturnType<typeof readLedger>> = {},
  ): ReturnType<typeof readLedger> => ({
    exists: true,
    base: 0.35,
    baseRows: 1,
    last: 0.355,
    lastIter: 57,
    ...over,
  })

  it('噪声带内 + 满额锚 ⇒ ★ 响亮确认（C 例：差 0.5pp 配 kk=1）', () => {
    const lines = composeBaselineLines({
      knobs: kk(),
      readings: rd(),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('起点-基线对照：起点 35.5%（账本末次评估 it57')
    expect(lines.join('\n')).toContain('vs 基线 35.0%（it0 = 课程 bc 权重） → 差 +0.5pp')
    expect(lines.join('\n')).toContain('★ 起点与基线只差 0.5pp')
    expect(lines.join('\n')).toContain('kl=0.90')
  })

  it('起点已低于基线 ⇒ ★ 另一支（熔断从第一个评估点就起算）', () => {
    const lines = composeBaselineLines({
      knobs: kk(),
      readings: rd({ last: 0.29, base: 0.35 }),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('★ 起点已低于基线 6.0pp')
    expect(lines.join('\n')).toContain('连续 3 点')
  })

  it('起点高于噪声带 ⇒ 只列对照行，不喊（上一腿确实涨了 = 正常）', () => {
    const lines = composeBaselineLines({
      knobs: kk(),
      readings: rd({ last: 0.42, base: 0.35 }),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('差 +7.0pp')
    expect(lines.join('\n')).not.toContain('★')
  })

  it('缰绳关 ⇒ 明说本腿不受锚（不给 ★：没有锚就没有回锚）', () => {
    const lines = composeBaselineLines({
      knobs: kk({ ref: false }),
      readings: rd(),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('kickstart 缰绳：关')
    expect(lines.join('\n')).not.toContain('★')
  })

  it('小 kk（< 响亮阈值）⇒ 不喊', () => {
    const lines = composeBaselineLines({
      knobs: kk({ init: 0.3 }),
      readings: rd(),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('kk 初值 0.3')
    expect(lines.join('\n')).not.toContain('★')
  })

  it('kk 初值 0 ⇒ 明说「锚实际不生效」（课程说开也不算数）', () => {
    const lines = composeBaselineLines({
      knobs: kk({ init: 0 }),
      readings: rd(),
      burn,
      logPath,
    })
    expect(lines.join('\n')).toContain('锚**实际不生效**')
  })

  it('声明初值却没开缰绳 ⇒ 先报「启动期会被拒启」', () => {
    const lines = composeBaselineLines({
      knobs: kk({ ref: false, contradiction: true }),
      readings: rd(),
      burn,
      logPath,
    })
    expect(lines[0]).toContain('被**拒启**')
    expect(lines.join('\n')).toContain('初值没有消费方')
  })

  it('缺读数三种形态各自说清（全缺 / 缺 it0 / 缺 it>0）', () => {
    const all = composeBaselineLines({
      knobs: kk(),
      readings: rd({ exists: false, base: null, last: null, lastIter: null }),
      burn,
      logPath,
    })
    expect(all.join('\n')).toContain('账本还没建')
    const noBase = composeBaselineLines({
      knobs: kk(),
      readings: rd({ base: null }),
      burn,
      logPath,
    })
    expect(noBase.join('\n')).toContain('基线缺（账本无 it0 行')
    expect(noBase.join('\n')).not.toContain('★')
    const noLast = composeBaselineLines({
      knobs: kk(),
      readings: rd({ last: null, lastIter: null }),
      burn,
      logPath,
    })
    expect(noLast.join('\n')).toContain('起点缺（账本暂无 it>0 评估点 = 新起点）')
  })
})

// ────────────────────────── ⑤ 端到端（真账本 + 真课程文件） ──────────────────────────

describe('kickstartReceipt：真账本 + 真课程文件（开课回执用它）', () => {
  it('x20 型课程（ref 开 + 缺省 kk=1 + 起点贴着基线）⇒ 对照行 + ★', () => {
    writeCourse('kk-e2e', { kickstart_ref: true, warmup_iters: 0, mode: 'per-tick' })
    writeLedger('kk-e2e', [summary(0, 0.355), summary(1, 0.34), summary(176, 0.352)])
    const text = kickstartReceipt('kk-e2e', null).join('\n')
    expect(text).toContain('kk 初值 1（来源：缺省 1.0 ——')
    expect(text).toContain('起点-基线对照：起点 35.2%（账本末次评估 it176')
    expect(text).toContain('vs 基线 35.5%（it0 = 课程 bc 权重） → 差 -0.3pp')
    expect(text).toContain('★ 起点与基线只差 0.3pp')
  })

  it('阈值覆盖随 rl-config 生效（margin 放大到 8pp ⇒ 差 6pp 也在带内）', () => {
    writeCourse('kk-e2e2', { kickstart_ref: true })
    writeLedger('kk-e2e2', [summary(0, 0.35), summary(9, 0.29)])
    const cfg = {
      version: 1,
      nodes: [],
      rl: { hub_port: 1, agent_port: 2, remote_token: 't' },
      courses: { 'kk-e2e2': { kickstart_burn: { margin_pp: 8, points: 2 } } },
    } as RlConfig
    const text = kickstartReceipt('kk-e2e2', cfg).join('\n')
    expect(text).toContain('连续 2 个评估点低于基线 8pp')
    expect(text).toContain('★ 起点与基线只差 6.0pp')
  })

  it('永不抛：坏课程文件 + 坏账本路径也只是少一行字', () => {
    writeFileSync(path.join(CUR, 'kk-broken.jsonc'), '{ // 半截', 'utf-8')
    const lines = kickstartReceipt('kk-broken', null)
    expect(lines.length).toBeGreaterThan(0)
    expect(lines.join('\n')).toContain('起点-基线对照：暂无（账本还没建')
  })
})

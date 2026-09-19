/**
 * rollout-src-launch-option.test.ts — M3「rollout 上云」开关进「启动训练」选项面
 *
 * 背景：`plan/remote-wire-remediation.plan.md` §5。M1（隧道）与 M2（瘦身）都做齐了
 * 「config → console-state → route → preset → UI」整链，M3 的 `rollout_src` 最初只
 * 有 python `--rollout-src` + 指标（`wire.rollout_src`）⇒ 想 A/B 只能手改
 * rl-config.json。本文件锁补上的那半条链路。
 *
 * 与 `slim`（`slim-launch-option.test.ts`）最关键的两点不同：
 *  1. **域无换算**：python `--rollout-src` 的 choices 就是 `'auto'|'local'|'node'`
 *     字符串，rl-config 里原样落。若有人在这里发明 `on/off` 之类的中间域，训练侧
 *     choices 会直接报错退出——所以有专项断言「preset 不得过换算函数」。
 *  2. **缺省不是 auto**：python 缺省 `auto`，但 `_rollout_source()` 在 rl-config
 *     没有该键时一律返回 `local`（历史行为）。控制台若缺省成 `node`，就会在没改过
 *     配置的课上谎报「本轮上云」。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { RlConfig } from '../src/core/types'
import { resolveRolloutSrc } from '../src/stack/specs'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const readSrc = (rel: string): string =>
  readFileSync(join(DASHBOARD_ROOT, ...rel.split('/')), 'utf8')

function cfg(
  rl: Record<string, unknown>,
  courses?: Record<string, Record<string, unknown>>,
): RlConfig {
  return {
    version: 1,
    nodes: [],
    rl: { hub_port: 1, agent_port: 2, remote_token: 't', ...rl },
    courses,
  } as RlConfig
}

describe('resolveRolloutSrc：生效值解析', () => {
  it('没配过 = local（不许谎报「上云」；python 缺省 auto 也解析成 local）', () => {
    expect(resolveRolloutSrc(cfg({}), '')).toBe('local')
    expect(resolveRolloutSrc(cfg({}), 's-dodge')).toBe('local')
    expect(resolveRolloutSrc(cfg({ rollout_src: 'auto' }), '')).toBe('auto')
  })

  it('rl.rollout_src 三值透传', () => {
    expect(resolveRolloutSrc(cfg({ rollout_src: 'node' }), '')).toBe('node')
    expect(resolveRolloutSrc(cfg({ rollout_src: 'local' }), '')).toBe('local')
  })

  it('per-course 覆盖 > rl.*（两个方向都要生效）', () => {
    const c = cfg({ rollout_src: 'node' }, { 's-dodge': { rollout_src: 'local' } })
    expect(resolveRolloutSrc(c, 's-dodge')).toBe('local') // 课覆盖回本机
    expect(resolveRolloutSrc(c, 'other')).toBe('node') // 其它课不受影响
    const c2 = cfg({ rollout_src: 'local' }, { 's-dodge': { rollout_src: 'node' } })
    expect(resolveRolloutSrc(c2, 's-dodge')).toBe('node')
    expect(resolveRolloutSrc(c2, 'other')).toBe('local')
  })

  it('坏值一律降级 local（不把未知值当上云；训练侧同样降级）', () => {
    expect(resolveRolloutSrc(cfg({ rollout_src: 'cloud' as unknown as 'node' }), '')).toBe('local')
    expect(resolveRolloutSrc(cfg({ rollout_src: 1 as unknown as 'node' }), '')).toBe('local')
  })
})

describe('preset / route / UI 接线（源码断言：跨文件链路 tsc 抓不到）', () => {
  it('route：rolloutSrc 走白名单，非法值 400（与 mode / cfProtocol 同写法）', () => {
    const src = readSrc('src/server/api/route.ts').replace(/\s+/g, ' ')
    expect(src).toContain("const rolloutSrc = bodyStr(body, 'rolloutSrc')")
    expect(src).toContain("if (rolloutSrc && !['auto', 'local', 'node'].includes(rolloutSrc))")
    expect(src).toContain('rolloutSrc: (rolloutSrc || undefined) as RolloutSrcMode | undefined')
  })

  it('preset：字符串域**原样**落 rl-config（过换算函数 = 训练启动直接报错退出）', () => {
    const src = readSrc('src/server/actions/preset.ts').replace(/\s+/g, ' ')
    expect(src).toContain('cfgT.rl.rollout_src = opts.rolloutSrc')
    expect(src).toMatch(/saveConsoleState\(\{ [^}]*rolloutSrc: opts\.rolloutSrc/)
    // 反向：不得像 slim 那样过任何 *ToCfg 换算
    expect(src).not.toMatch(/rollout_src = \w*[Tt]oCfg\(/)
    // 回写门必须带上它，否则「选了但没写盘」= 假成功
    expect(src).toContain('opts.cfProtocol || opts.cfEdgeIp || opts.slim || opts.rolloutSrc')
  })

  it('state-view：modes 带当前生效值（UI 才能显示「改动有没有生效」）', () => {
    const src = readSrc('src/server/api/state-view.ts').replace(/\s+/g, ' ')
    expect(src).toContain('rolloutSrc: resolveRolloutSrc(cfg, course)')
  })

  it('app.tsx：rolloutSrc 进 preset body（漏了 = 选项点了不生效的假成功）', () => {
    const src = readSrc('src/web/app/app.tsx').replace(/\s+/g, ' ')
    expect(src).toContain('if (opts?.rolloutSrc) body.rolloutSrc = opts.rolloutSrc')
  })

  it('TrainLaunchModal：控件 + 随启动选项带上 rolloutSrc + 显示当前生效值', () => {
    const src = readSrc('src/web/app/panels/TrainLaunchModal.tsx').replace(/\s+/g, ' ')
    expect(src).toContain("const TC_ROLLOUT_SRC = 'tc.rolloutSrc'")
    expect(src).toContain('ariaLabel="rollout 执行位置"')
    // 上抛的选项对象里有它（漏了 = UI 选了但没随 onLaunch 传出去；启动不再带 mode）
    expect(src).toMatch(/onLaunch\(\{[^}]*\brolloutSrc\b/)
    // 选项对象类型里有它
    expect(src).toContain('rolloutSrc: RolloutSrcMode }')
    // 上次选择要记住（与 cfProtocol / slim 同口径）
    expect(src).toContain('writeLocal(TC_ROLLOUT_SRC, rolloutSrc)')
    // 当前生效值上屏：以为改了其实没改是本仓反复出现的一类坑
    expect(src).toContain("modes.rolloutSrc === 'node'")
  })

  it('console-state：字段 additive（旧文件无此键不阻塞读取，由 rl-config 回填）', () => {
    const src = readSrc('src/server/actions/console-state.ts').replace(/\s+/g, ' ')
    expect(src).toContain('rolloutSrc?: RolloutSrcMode')
    // 缺省状态必须**不带**此键——带了就把「没配过」写死成了某个值
    // （trainerPpo 已随「启动不选模式」退役，DEFAULT_STATE 只剩两个课程字段）
    expect(src).toContain("const DEFAULT_STATE: ConsoleState = { course: '', activeCourse: '' }")
    expect(src).not.toMatch(/const DEFAULT_STATE[^\n]*rolloutSrc/)
  })
})

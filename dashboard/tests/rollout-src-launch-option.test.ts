/**
 * rollout-src-launch-option.test.ts — M3「rollout 上云」开关进「启动训练」选项面
 *
 * 背景：`plan/remote-wire-remediation.plan.md` §5。M1（隧道）与 M2（瘦身）都做齐了
 * 「config → console-state → route → preset → UI」整链，M3 的 `rollout_src` 最初只
 * 有 python `--rollout-src` + 指标（`wire.rollout_src`）⇒ 想 A/B 只能手改
 * rl-config.json。本文件锁补上的那半条链路。
 *
 * 与 `slim`（`slim-launch-option.test.ts`）最关键的两点不同：
 *  1. **域无换算**：python `--rollout-src` 的 choices 就是
 *     `'auto'|'local'|'node'|'run'` 字符串，rl-config 里原样落。若有人在这里发明
 *     `on/off` 之类的中间域，训练侧 choices 会直接报错退出——所以有专项断言
 *     「preset 不得过换算函数」。（`run` = 离线训练模式的整段上云，2026-09-19 加。）
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

  it('rl.rollout_src 各值透传（含离线模式的 run）', () => {
    expect(resolveRolloutSrc(cfg({ rollout_src: 'node' }), '')).toBe('node')
    expect(resolveRolloutSrc(cfg({ rollout_src: 'local' }), '')).toBe('local')
    // run = 离线训练模式（整段 job 交给云机）：漏掉它，离线课会在 UI 上显示成 local，
    // 而那正是「云机在跑 / 本机在跑看起来一样」的静默分叉。
    expect(resolveRolloutSrc(cfg({ rollout_src: 'run' }), '')).toBe('run')
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
    expect(src).toContain(
      "if (rolloutSrc && !['auto', 'local', 'node', 'run'].includes(rolloutSrc))",
    )
    expect(src).toContain('rolloutSrc: (rolloutSrc || undefined) as RolloutSrcMode | undefined')
  })

  it('★ 全局 rl.rollout_src 不再被控制台写（课程级选项只落 courses.<课>）', () => {
    // 2026-09-20：rollout 位置随「开课」走，而它在 rl-config 里的落点是**课程级覆盖**。
    // 曾经的写法（全局面 opt-in）把一门课的选择变成了所有课共用的默认面。
    const preset = readSrc('src/server/actions/preset.ts').replace(/\s+/g, ' ')
    expect(preset).not.toMatch(/rl\.rollout_src\s*=/)
    // 字符串域**原样**落（过换算函数 = 训练启动直接报错退出）
    const life = readSrc('src/server/actions/course-lifecycle.ts').replace(/\s+/g, ' ')
    expect(life).toContain('row.rollout_src = opts.rolloutSrc')
    expect(life).not.toMatch(/rollout_src = \w*[Tt]oCfg\(/)
  })

  it('state-view：modes 带当前生效值（UI 才能显示「改动有没有生效」）', () => {
    const src = readSrc('src/server/api/state-view.ts').replace(/\s+/g, ' ')
    expect(src).toContain('rolloutSrc: resolveRolloutSrc(cfg, course)')
  })

  it('app.tsx：rolloutSrc 进 **openCourse** body（漏了 = 选项点了不生效的假成功）', () => {
    const src = readSrc('src/web/app/app.tsx').replace(/\s+/g, ' ')
    expect(src).toContain('...(opts.rolloutSrc ? { rolloutSrc: opts.rolloutSrc } : {})')
    // 启动 body 不带它（课程级选项不得回流）
    expect(src).not.toContain('body.rolloutSrc')
  })

  it('开课弹窗：控件 + 随开课选项带上 rolloutSrc + 显示当前生效值', () => {
    const src = readSrc('src/web/app/panels/OpenCourseModal.tsx').replace(/\s+/g, ' ')
    expect(src).toContain("const TC_OPEN_ROLLOUT = 'tc.openCourse.rolloutSrc'")
    expect(src).toContain('ariaLabel="rollout 执行位置"')
    // 上抛的选项对象里有它（漏了 = UI 选了但没随 onConfirm 传出去）
    expect(src).toMatch(/onConfirm\(\{[^}]*\brolloutSrc\b/)
    // 上次选择要记住（与 cfProtocol / slim 同口径）
    expect(src).toContain('writeLocal(TC_OPEN_ROLLOUT, rolloutSrc)')
    // 当前生效值上屏：以为改了其实没改是本仓反复出现的一类坑
    expect(src).toContain('modes.rolloutSrc')
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

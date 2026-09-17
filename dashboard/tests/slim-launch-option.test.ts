/**
 * slim-launch-option.test.ts — M2 协议瘦身开关进「启动训练」选项面
 *
 * 背景：`plan/remote-wire-remediation.plan.md` §1.4 要求每个改动都有**运行期开关**
 * （rl-config 键 + console-state），且「开关取值必须写进 iteration 事件」以便事后按
 * 选项分组统计。M1 的隧道选项走齐了（config → console-state → route → preset → UI），
 * M2 的 `slim` 只落了 config 键 + 指标 ⇒ 想 A/B 只能手改 rl-config.json，等于没有
 * 「启动时提供选项」这条路。本文件锁补上的那半条链路。
 *
 * 双域是本条最容易写错的地方：UI/console-state 用 `'on'|'off'`，rl-config 必须落
 * `1|0`（python `--remote-slim` 是 `type=int, choices=(0,1)`，写字符串会让训练启动
 * 直接报错退出）。所以 `slimToCfg` 是**唯一**换算入口，且有专项断言。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { RlConfig } from '../src/core/types'
import { resolveSlim, slimToCfg } from '../src/stack/specs'

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

describe('resolveSlim：生效值解析', () => {
  it('没配过 = on（python `_d("slim", 1)` 同口径，不许谎报「关」）', () => {
    expect(resolveSlim(cfg({}), '')).toBe('on')
    expect(resolveSlim(cfg({}), 's-dodge')).toBe('on')
  })

  it('rl.slim=0 → off；=1 → on（数字域容错：字符串 "0" 也认）', () => {
    expect(resolveSlim(cfg({ slim: 0 }), '')).toBe('off')
    expect(resolveSlim(cfg({ slim: 1 }), '')).toBe('on')
    expect(resolveSlim(cfg({ slim: '0' as unknown as 0 }), '')).toBe('off')
  })

  it('per-course 覆盖 > rl.*（两个方向都要生效）', () => {
    const c = cfg({ slim: 1 }, { 's-dodge': { slim: 0 } })
    expect(resolveSlim(c, 's-dodge')).toBe('off') // 课覆盖关掉
    expect(resolveSlim(c, 'other')).toBe('on') // 其它课不受影响
    const c2 = cfg({ slim: 0 }, { 's-dodge': { slim: 1 } })
    expect(resolveSlim(c2, 's-dodge')).toBe('on') // 课覆盖打开
    expect(resolveSlim(c2, 'other')).toBe('off')
  })
})

describe('slimToCfg：唯一换算入口', () => {
  it('on→1 / off→0（写字符串进 rl-config 会让训练启动报错，故必须有此断言）', () => {
    expect(slimToCfg('on')).toBe(1)
    expect(slimToCfg('off')).toBe(0)
  })

  it('往返一致：resolveSlim 的结果回写后语义不变', () => {
    for (const mode of ['on', 'off'] as const) {
      expect(resolveSlim(cfg({ slim: slimToCfg(mode) }), '')).toBe(mode)
    }
  })
})

describe('preset / route / UI 接线（源码断言：跨文件链路 tsc 抓不到）', () => {
  it('route：slim 走白名单，非法值 400（与 mode / cfProtocol 同写法）', () => {
    const src = readSrc('src/server/api/route.ts').replace(/\s+/g, ' ')
    expect(src).toContain("const slim = bodyStr(body, 'slim')")
    expect(src).toContain("if (slim && !['on', 'off'].includes(slim))")
    expect(src).toContain('slim: (slim || undefined) as SlimMode | undefined')
  })

  it('preset：写 rl-config 必须过 slimToCfg（数值域），console-state 存 UI 域', () => {
    const src = readSrc('src/server/actions/preset.ts').replace(/\s+/g, ' ')
    expect(src).toContain('cfgT.rl.slim = slimToCfg(opts.slim)')
    expect(src).toMatch(/saveConsoleState\(\{ [^}]*slim: opts\.slim/)
    // 反向：不得把字符串直接写进 rl-config
    expect(src).not.toMatch(/cfgT\.rl\.slim = opts\.slim\b/)
  })

  it('state-view：modes 带当前生效值（UI 才能显示「改动有没有生效」）', () => {
    const src = readSrc('src/server/api/state-view.ts').replace(/\s+/g, ' ')
    expect(src).toContain('slim: resolveSlim(cfg, course)')
  })

  it('app.tsx：slim 进 preset body（漏了 = 选项点了不生效的假成功）', () => {
    const src = readSrc('src/web/app/app.tsx').replace(/\s+/g, ' ')
    expect(src).toContain('if (push?.slim) body.slim = push.slim')
  })

  it('TrainLaunchModal：控件 + 随启动选项带上 slim + 显示当前生效值', () => {
    const src = readSrc('src/web/app/panels/TrainLaunchModal.tsx').replace(/\s+/g, ' ')
    expect(src).toContain("const TC_SLIM = 'tc.slim'")
    expect(src).toContain('ariaLabel="协议瘦身"')
    // 顺序无关（后续 M3 又往同一个选项对象里加了 rolloutSrc——写死整行会让本断言
    // 在「别人加开关」时变红，而那不是回归）。
    expect(src).toMatch(/const tunnel: TunnelLaunchOpts = \{[^}]*\bslim\b/)
    // 上次选择要记住（与 cfProtocol 同口径）
    expect(src).toContain('writeLocal(TC_SLIM, slim)')
    // 当前生效值上屏：以为改了其实没改是本仓反复出现的一类坑
    expect(src).toContain("modes.slim === 'off' ? '关' : '开'")
  })
})

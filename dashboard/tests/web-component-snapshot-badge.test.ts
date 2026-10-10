/**
 * web-component-snapshot-badge.test.ts — 组件卡上的**集群代码快照**徽章（plan/cluster-code-snapshot 的 P2）。
 *
 * 分层：`src/web/app/panels/ComponentCards.tsx::codeSnapshotBadge`（纯函数）+ 整面板 SSR 形状。
 *
 * 为什么单独一个文件：这枚徽章回答的是「本会话跑的是哪份代码」——打包时机已上移到会话启动
 * （`remote-transport.md §79`），所以它在会话期间是常量，**贴错行就等于告诉操作员错的锚**
 * （例如把 hub 建的快照贴到 trainer 行上）。两条判据各有自己的失效面，都要单测：
 *   ① 锚种类 → 组件行（`hub` → hubServer；`trainer`/`export`/`run_rl` → trainingLoop）；
 *   ② 认不出的种类 / 没有快照 ⇒ **不贴**（不猜、不假装）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { ComponentCards, codeSnapshotBadge } from '../src/web/app/panels/ComponentCards'
import type { CodeSnapshotView, ComponentView, ConsoleStateView } from '../src/web/view'

const SHA = `${'ab12cd34ef56'.padEnd(64, '0')}`

const snap = (over: Partial<CodeSnapshotView> = {}): CodeSnapshotView => ({
  sha12: 'ab12cd34ef56',
  sha256: SHA,
  zip: 'code.ab12cd34ef56.zip',
  packedAtEpoch: 1_786_442_686,
  anchorKind: 'hub',
  anchorPid: 20512,
  anchorAlive: true,
  ...over,
})

function cv(key: string): ComponentView {
  return {
    key,
    label: key,
    status: 'running',
    pid: 1,
    url: null,
    course: null,
    mode: null,
    healthy: true,
    log: null,
    logTail: [],
    busy: false,
    scope: 'shared',
  }
}

/** 面板只要 `components` / `pushFleet` / `codeSnapshot` 三块（其余字段与渲染无关）。 */
function state(over: Partial<ConsoleStateView> = {}): ConsoleStateView {
  return {
    time: 't',
    course: 'x',
    courses: ['x'],
    components: [cv('hubServer'), cv('trainingLoop'), cv('cloudflared')],
    pushFleet: null,
    codeSnapshot: null,
    ...over,
  } as unknown as ConsoleStateView
}

const html = (s: ConsoleStateView): string =>
  renderToString(
    h(ComponentCards, {
      stateView: s,
      onAction: async () => ({ ok: true }),
      onLaunchTrainer: () => {},
    }),
  )

describe('codeSnapshotBadge（锚 → 行）', () => {
  it('hub 锚贴 hubServer 行；trainer/export/run_rl 贴 trainingLoop 行', () => {
    expect(codeSnapshotBadge(snap({ anchorKind: 'hub' }), 'hubServer')?.text).toBe(
      'code ab12cd34ef56',
    )
    expect(codeSnapshotBadge(snap({ anchorKind: 'hub' }), 'trainingLoop')).toBeNull()
    for (const kind of ['trainer', 'export', 'run_rl'] as const) {
      expect(codeSnapshotBadge(snap({ anchorKind: kind }), 'trainingLoop')?.text).toBe(
        'code ab12cd34ef56',
      )
      expect(codeSnapshotBadge(snap({ anchorKind: kind }), 'hubServer')).toBeNull()
    }
  })

  it('没有快照 / 认不出的锚种类 ⇒ 不贴（不猜）', () => {
    for (const s of [null, undefined]) {
      expect(codeSnapshotBadge(s, 'hubServer')).toBeNull()
      expect(codeSnapshotBadge(s, 'trainingLoop')).toBeNull()
    }
    expect(codeSnapshotBadge(snap({ anchorKind: '' }), 'hubServer')).toBeNull()
    expect(codeSnapshotBadge(snap({ anchorKind: 'mac' }), 'hubServer')).toBeNull()
  })

  it('悬停给全量（zip / 完整 sha / 锚）；锚已亡时点名（文字不变，仍是那份代码）', () => {
    const alive = codeSnapshotBadge(snap(), 'hubServer')!
    expect(alive.title).toContain('code.ab12cd34ef56.zip')
    expect(alive.title).toContain(`sha256 ${SHA}`)
    expect(alive.title).toContain('hub/pid=20512')
    expect(alive.title).not.toContain('锚已亡')
    const dead = codeSnapshotBadge(snap({ anchorAlive: false }), 'hubServer')!
    expect(dead.text).toBe(alive.text)
    expect(dead.title).toContain('锚已亡')
  })
})

/** 徽章落在**哪一行**：按行容器切块，取含徽章的那一块。
 *
 *  为什么不用「找角色名的字符位置」：族标题的悬停提示把「门房 / 跑腿 / 管事 / 丹徒」四个词
 *  都写了一遍（`SectionHeader.hint`）⇒ `indexOf('门房')` 命中的是提示文本，不是行名。
 *  切块判据只看**行容器**，与提示措辞无关。 */
function badgeRow(out: string): string | undefined {
  return out.split('<div class="tc-row-wrap">').find((r) => r.includes('code ab12cd34ef56'))
}

describe('ComponentCards SSR：徽章上屏位置', () => {
  it('hub 建的快照 ⇒ 落在 hubServer 那一行', () => {
    const out = html(state({ codeSnapshot: snap() }))
    expect(out).toContain('tc-cc__snap')
    const row = badgeRow(out)
    expect(row).toBeDefined()
    expect(row).toContain('hubServer')
    expect(row).not.toContain('trainingLoop')
  })

  it('trainer / run_rl 建的快照 ⇒ 落在 trainingLoop 那一行', () => {
    for (const anchorKind of ['trainer', 'run_rl'] as const) {
      const row = badgeRow(html(state({ codeSnapshot: snap({ anchorKind }) })))
      expect(row).toBeDefined()
      expect(row).toContain('trainingLoop')
      expect(row).not.toContain('hubServer')
    }
  })

  it('没有快照 ⇒ 一枚徽章都不出（不假装有一份）', () => {
    expect(html(state())).not.toContain('tc-cc__snap')
  })
})

/** web-bc-eval-cell.test.ts — BC Epoch 表 evalA 单元格（SSR；事件不在此测）。 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { BcEvalCell } from '../src/web/app/panels/BcPanel'

const noop = (): void => {}

describe('BcEvalCell', () => {
  it('未评且 eval 开启 ⇒ 渲染 evalA 按钮', () => {
    const html = renderToString(
      h(BcEvalCell, {
        row: { it: 2, epoch: 17, hasEval: false },
        enabled: true,
        running: false,
        onEval: noop,
      }),
    )
    expect(html).toContain('>evalA</button>')
    expect(html).toContain('it2 ep17')
  })

  it('已评 ⇒ 显示「已评」，不再给按钮', () => {
    const html = renderToString(
      h(BcEvalCell, {
        row: { it: 2, epoch: 17, hasEval: true },
        enabled: true,
        running: false,
        onEval: noop,
      }),
    )
    expect(html).toContain('已评')
    expect(html).not.toContain('evalA')
  })

  it('eval 未启用或正在跑 ⇒ 不出现可点的 evalA', () => {
    const disabled = renderToString(
      h(BcEvalCell, {
        row: { it: 1, epoch: 1, hasEval: false },
        enabled: false,
        running: false,
        onEval: noop,
      }),
    )
    expect(disabled).not.toContain('evalA')
    expect(disabled).toContain('—')

    const running = renderToString(
      h(BcEvalCell, {
        row: { it: 1, epoch: 1, hasEval: false },
        enabled: true,
        running: true,
        onEval: noop,
      }),
    )
    expect(running).toContain('disabled')
    expect(running).toContain('…')
    expect(running).not.toContain('>evalA</button>')
  })
})

import { describe, expect, it } from 'bun:test'
import { createShipLoop } from '../src/notebook-companion/app/ship-loop'
import { DEFAULT_CONFIG } from '../src/notebook-companion/config'
import { StubFrameAdapter } from '../src/notebook-companion/sites/stub'
import type { Runtime } from '../src/runtime'

function loop(referrer = '') {
  return createShipLoop({
    doc: {} as unknown as Document,
    href: 'https://kkb-production.jupyter-proxy.kaggle.net/static/assets/jupyterlab-v4/index.html?session=REDACTED',
    referrer,
    config: DEFAULT_CONFIG,
    runtime: {} as unknown as Runtime,
    adapter: new StubFrameAdapter('kaggle'),
    pageTag: 'geooms',
  })
}

describe('ship-loop：日志会话键用 notebook 身份', () => {
  it('frame 自己只能推出 unknown；host 的基替换它并保留页签', () => {
    const l = loop()
    expect(l.session).toBe('kaggle-unknown-edit-geooms')
    l.setSessionBase('kaggle-huang-rollout-edit')
    expect(l.session).toBe('kaggle-huang-rollout-edit-geooms')
    // 非法基（空格/符号）一律忽略，绝不把日志名写成乱七八糟的东西
    l.setSessionBase('bad base!')
    expect(l.session).toBe('kaggle-huang-rollout-edit-geooms')
    l.stop()
  })

  it('referrer 已能解析出 notebook 时不被空基覆盖', () => {
    const l = loop('https://www.kaggle.com/code/Huang/rollout/edit')
    // parseKaggleSession / withPageTag 一律小写（与服务端会话键同一口径）
    expect(l.session).toBe('kaggle-huang-rollout-edit-geooms')
    l.setSessionBase('')
    expect(l.session).toBe('kaggle-huang-rollout-edit-geooms')
    l.stop()
  })
})

import { describe, expect, it } from 'bun:test'
import { createShipLoop } from '../src/notebook-companion/app/ship-loop'
import { DEFAULT_CONFIG } from '../src/notebook-companion/config'
import { StubFrameAdapter } from '../src/notebook-companion/sites/stub'
import type { FrameAdapter } from '../src/notebook-companion/sites/types'
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

/** 一个 code cell + 固定输出；`command()` 记一次「清空」。 */
function fakeAdapter(output: string, onClear: () => void): FrameAdapter {
  return {
    site: 'kaggle',
    ready: true,
    cells: () => [{ id: 'c1', index: 0, kind: 'code' as const }],
    outputText: () => output,
    sourceText: () => '',
    activeCell: () => null,
    ensureRendered: async () => null,
    select: async () => ({ ok: true }),
    runCell: async () => ({ ok: true }),
    clearCellOutput: async () => ({ ok: true }),
    toggleCell: async () => ({ ok: true }),
    setFoldedAll: async () => ({ ok: true }),
    setFolded: async () => ({ ok: true }),
    command: async () => {
      onClear()
      return { ok: true }
    },
    scroll: () => ({ ok: true }),
    scrollOuter: () => ({ ok: true }),
    flash: () => {},
    focusConfig: async () => ({ ok: true }),
    setScheduledMarks: () => ({ ok: true }),
  }
}

/** 搬运接口永远 200（auto-save 只关心 drain 成功后才清空）。 */
const okRuntime = {
  gmRequest: async () => ({
    status: 200,
    statusText: 'OK',
    responseText: JSON.stringify({ ok: true, epoch: 1 }),
    responseHeaders: '',
  }),
} as unknown as Runtime

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

  it('site=colab：会话键从 /drive/<fileId> 解析，不套 Kaggle 的 owner/slug 规则', () => {
    const l = createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://colab.research.google.com/drive/1AbC-dEf',
      referrer: '',
      config: DEFAULT_CONFIG,
      runtime: {} as unknown as Runtime,
      adapter: new StubFrameAdapter('colab'),
      site: 'colab',
      pageTag: 'ab12cd',
    })
    expect(l.session).toBe('colab-1abc-def-ab12cd')
    l.stop()
  })
})

describe('ship-loop：saveAndClear 的失败归因', () => {
  /** 输出按脚本逐次取（用完停在上一个值）：模拟「发送窗口里输出还在长」。 */
  function growingAdapter(script: string[]): { adapter: FrameAdapter; clears: () => number } {
    let i = 0
    let clears = 0
    const next = (): string => {
      const v = script[Math.min(i, script.length - 1)] ?? ''
      i += 1
      return v
    }
    return {
      adapter: {
        site: 'kaggle',
        ready: true,
        cells: () => [{ id: 'c1', index: 0, kind: 'code' as const }],
        outputText: next,
        sourceText: () => '',
        activeCell: () => null,
        ensureRendered: async () => null,
        select: async () => ({ ok: true }),
        runCell: async () => ({ ok: true }),
        clearCellOutput: async () => ({ ok: true }),
        toggleCell: async () => ({ ok: true }),
        setFoldedAll: async () => ({ ok: true }),
        setFolded: async () => ({ ok: true }),
        command: async () => {
          clears += 1
          return { ok: true }
        },
        scroll: () => ({ ok: true }),
        scrollOuter: () => ({ ok: true }),
        flash: () => {},
        focusConfig: async () => ({ ok: true }),
        setScheduledMarks: () => ({ ok: true }),
      },
      clears: () => clears,
    }
  }

  function loopWith(adapter: FrameAdapter, runtime: Runtime) {
    return createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://www.kaggle.com/code/a/b/edit',
      config: DEFAULT_CONFIG,
      runtime,
      adapter,
      now: () => 1_000_000,
      writer: 'w1',
    })
  }

  it('另一个标签持租约 ⇒ 报 writer 冲突（本标签被动），不冒充 offset 冲突、绝不清空', async () => {
    const { adapter, clears } = growingAdapter(['a'])
    const runtime = {
      gmRequest: async () => ({
        status: 409,
        statusText: 'Conflict',
        responseText: JSON.stringify({
          ok: false,
          reason: 'writer-conflict',
          leaseRemainingMs: 1000,
        }),
        responseHeaders: '',
      }),
    } as unknown as Runtime
    const l = loopWith(adapter, runtime)
    const r = await l.saveAndClear()
    expect(r.ok).toBe(false)
    expect(r.reason).toContain('writer 冲突')
    expect(clears()).toBe(0)
    l.stop()
  })

  it('发送窗口里输出继续长 ⇒ 下一轮接着发，不报 offset 冲突', async () => {
    // 调用序（每轮 runOnce 一次 + 判游标一次）：round2 读到 'a'（无新字节）后
    // 判游标时已是 'ab' —— 旧逻辑在这里直接失败，正解是下一轮把 'b' 发出去。
    const { adapter, clears } = growingAdapter(['a', 'a', 'a', 'ab'])
    const l = loopWith(adapter, okRuntime)
    const r = await l.saveAndClear()
    expect(r).toEqual({ ok: true, bytes: 2 })
    expect(clears()).toBe(1)
    l.stop()
  })
})

describe('ship-loop：周期性自动保存并清空', () => {
  it('到间隔才执行；近 10s 有交互就延后；空转不重复清', async () => {
    let t = 1_000_000
    let clears = 0
    const l = createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://www.kaggle.com/code/a/b/edit',
      config: { ...DEFAULT_CONFIG, autoSaveIntervalSec: 60 },
      runtime: okRuntime,
      adapter: fakeAdapter('hello', () => {
        clears += 1
      }),
      now: () => t,
      writer: 'w1',
    })

    // 刚创建：还没到第一个周期
    expect(await l.autoSaveIfNeeded()).toEqual({ saved: false, bytes: 0 })

    t += 61_000
    const first = await l.autoSaveIfNeeded()
    expect(first.saved).toBe(true)
    expect(first.bytes).toBeGreaterThan(0)
    expect(clears).toBe(1)

    t += 1_000
    expect((await l.autoSaveIfNeeded()).saved).toBe(false)

    // 下一次到点但用户刚操作过 ⇒ 延后；计时不前进，空闲后同一次到点补做
    t += 60_000
    l.lastInteractionAt(t - 1_000)
    const deferred = await l.autoSaveIfNeeded()
    expect(deferred).toEqual({ saved: false, bytes: 0, reason: 'deferred' })
    expect(clears).toBe(1)

    t += 10_000
    expect((await l.autoSaveIfNeeded()).saved).toBe(true)
    expect(clears).toBe(2)
    l.stop()
  })

  it('setAutoSave：立即切换开关/周期，并从此刻重新计时', async () => {
    let t = 1_000_000
    let clears = 0
    const l = createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://www.kaggle.com/code/a/b/edit',
      config: { ...DEFAULT_CONFIG, autoSaveEnabled: false, autoSaveIntervalSec: 60 },
      runtime: okRuntime,
      adapter: fakeAdapter('hello', () => {
        clears += 1
      }),
      now: () => t,
    })
    t += 61_000
    expect((await l.autoSaveIfNeeded()).saved).toBe(false) // 总开关关着

    l.setAutoSave(true, 120)
    t += 61_000 // 新周期 120s 未到
    expect((await l.autoSaveIfNeeded()).saved).toBe(false)
    t += 60_000 // 距切换 121s
    expect((await l.autoSaveIfNeeded()).saved).toBe(true)
    expect(clears).toBe(1)

    l.setAutoSave(false, 60)
    t += 61_000
    expect((await l.autoSaveIfNeeded()).saved).toBe(false)
    expect(clears).toBe(1)
    l.stop()
  })

  it('间隔 0 = 关闭；没有输出 ⇒ 只重置计时、不清空', async () => {
    let t = 5_000_000
    let clears = 0
    const off = createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://www.kaggle.com/code/a/b/edit',
      config: { ...DEFAULT_CONFIG, autoSaveIntervalSec: 0 },
      runtime: okRuntime,
      adapter: fakeAdapter('hello', () => {
        clears += 1
      }),
      now: () => t,
    })
    t += 3_600_000
    expect((await off.autoSaveIfNeeded()).saved).toBe(false)
    expect(clears).toBe(0)

    const empty = createShipLoop({
      doc: {} as unknown as Document,
      href: 'https://www.kaggle.com/code/a/b/edit',
      config: { ...DEFAULT_CONFIG, autoSaveIntervalSec: 60 },
      runtime: okRuntime,
      adapter: fakeAdapter('', () => {
        clears += 1
      }),
      now: () => t,
    })
    t += 61_000
    expect((await empty.autoSaveIfNeeded()).saved).toBe(false)
    expect(clears).toBe(0)
    off.stop()
    empty.stop()
  })
})

/**
 * web-col-order.test.ts — 列顺序偏好（用户 2026-11-10：指标表各列支持拖拽表头自定义位置）。
 *
 * 分层：src/web/view/col-order.ts（纯函数）+ components/col-drag.ts（拖拽属性工厂）
 *       + DataTable 的 `reorderable` 开关（渲染级）。
 *
 * 四个钉子：
 *  ① 解析：坏 JSON / 非字符串元素 ⇒ 「没有顺序」；重复键只留第一次；
 *  ② 应用：认识的键按存储序，存储里没有的键回到**模型位**（新列不消失，也不会因为另一个
 *     视图没存过它就被甩到表尾；2026-11-10 报障）；
 *  ③ 移动：from 插到 to 之前；任一键缺失 ⇒ 原样返回（陈旧存储不许制造乱序）；
 *  ④ 接线：两张表（`/metrics` 与首页速览表）共用同一份 colDragProps，顺序各存各的键。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { DataTable } from '../src/web/components/DataTable'
import {
  applyColOrder,
  BEHAVIOR_METRICS,
  behaviorColKey,
  EVAL_ROW_KEYS,
  METRIC_COLS,
  METRIC_ORDER_KEYS,
  METRIC_ORDER_REFS,
  moveColKey,
  parseColOrder,
  resolveColOrder,
  ROLLOUT_ROW_KEYS,
} from '../src/web/view'

const KEYS = ['iter', 'time', 'winRate', 'kl'] as const
const COLS = KEYS.map((key) => ({ key }))
const keyOf = (cols: readonly { key: string }[]): string[] => cols.map((c) => c.key)

describe('parseColOrder：存储 → 键序', () => {
  it('合法数组保序；重复键只留第一次', () => {
    expect(parseColOrder(JSON.stringify(['kl', 'iter']))).toEqual(['kl', 'iter'])
    expect(parseColOrder(JSON.stringify(['kl', 'iter', 'kl']))).toEqual(['kl', 'iter'])
    expect(parseColOrder('[]')).toEqual([])
  })

  it('坏值 / 非字符串元素 ⇒ null（没顺序 = 模型顺序），永不抛', () => {
    for (const raw of [null, undefined, '', 'x', '{}', '["a"', '[1,2]', '["a",2]']) {
      expect(parseColOrder(raw)).toBeNull()
    }
  })
})

describe('applyColOrder：键序 → 列序', () => {
  it('没有顺序 / 空数组 / 单列 ⇒ 原样（模型顺序）', () => {
    expect(keyOf(applyColOrder(COLS, null))).toEqual([...KEYS])
    expect(keyOf(applyColOrder(COLS, []))).toEqual([...KEYS])
    expect(keyOf(applyColOrder([{ key: 'iter' }], ['kl']))).toEqual(['iter'])
  })

  it('认识的键按存储序；缺键回到模型位（不是一律接尾）', () => {
    // kl 的模型前驱是 winRate ⇒ 跟着它（不是一律甩到表尾）
    expect(keyOf(applyColOrder(COLS, ['winRate', 'iter']))).toEqual([
      'winRate',
      'kl',
      'iter',
      'time',
    ])
    // 用户把 kl 拖到最前、winRate 第二 ⇒ 缺的 iter/time 回到模型位
    expect(keyOf(applyColOrder(COLS, ['kl', 'winRate']))).toEqual(['iter', 'time', 'kl', 'winRate'])
  })

  it('存储里的陌生键被忽略（别表的键不会变出幽灵列，也不会挤掉本表的列）', () => {
    // 只有 kl 是认识的（它在模型里排最后）⇒ 缺的 iter/time/winRate 回模型位，排它前面
    expect(keyOf(applyColOrder(COLS, ['bm:ticks', 'kl']))).toEqual([
      'iter',
      'time',
      'winRate',
      'kl',
    ])
  })

  it('可见子集（藏过列）保住存储里的相对顺序', () => {
    const visible = COLS.filter((c) => c.key !== 'time')
    expect(keyOf(applyColOrder(visible, ['kl', 'iter']))).toEqual(['kl', 'iter', 'winRate'])
  })

  it('不改入参（渲染期反复调用不许把模型顺序改掉）', () => {
    const order = ['kl']
    applyColOrder(COLS, order)
    expect(keyOf(COLS)).toEqual([...KEYS])
    expect(order).toEqual(['kl'])
  })
})

describe('跨视图：一个存储键服务列集合不同的多个视图（2026-11-10 报障）', () => {
  // eval 视图的真实形状（简化成同一段）：eval 胜率排在 time 之后。
  const EVAL_COLS = ['iter', 'time', 'evalRate', 'b01', 'avgWinTicks', 'scoreMean'].map((key) => ({
    key,
  }))
  // 「全部」视图拖动后写下的顺序（不含 eval 专属列——列集合里根本没有它们）。
  const ALL_DRAG = ['winRate', 'iter', 'time', 'avgWinTicks', 'kills', 'scoreMean']

  it('旧存储缺 eval 专属列 ⇒ eval 胜率回到 time 之后（不再被甩到尾）', () => {
    expect(keyOf(applyColOrder(EVAL_COLS, ALL_DRAG))).toEqual([
      'iter',
      'time',
      'evalRate',
      'b01',
      'avgWinTicks',
      'scoreMean',
    ])
  })

  it('拖拽基准用键全集：顺序表含两个视图的键，切视图不再丢位置', () => {
    const base = resolveColOrder(METRIC_ORDER_KEYS, null)
    const dragged = moveColKey(base, 'winRate', 'iter')
    expect(dragged).toContain('evalRate')
    expect(dragged.indexOf('winRate')).toBeLessThan(dragged.indexOf('iter'))
    // 同一份顺序套到 eval 视图：eval 胜率仍在 time 后面（前三个位置不变）。
    expect(keyOf(applyColOrder(EVAL_COLS, dragged)).slice(0, 3)).toEqual([
      'iter',
      'time',
      'evalRate',
    ])
  })

  it('藏起来的列保住拖拽后的位置（取消隐藏不回表尾）', () => {
    const visible = COLS.filter((c) => c.key !== 'kl') // kl 被藏起来，没参与拖动
    const afterDrag = moveColKey(resolveColOrder(KEYS, null), 'winRate', 'iter')
    expect(keyOf(applyColOrder(visible, afterDrag))).toEqual(['winRate', 'iter', 'time'])
    // 取消隐藏：kl 仍在拖拽后该在的地方（这里是表尾，但它被 pos 记住而不是被当新列）
    expect(keyOf(applyColOrder(COLS, afterDrag))).toEqual(['winRate', 'iter', 'time', 'kl'])
  })
})

describe('两个视图互不牵扯（2026-11-10 报障：改 rollout 胜率会动 eval 胜率）', () => {
  const rollout = ['iter', 'time', 'winRate', 'avgWinTicks', 'kills', 'scoreMean']
  const evalCols = ['iter', 'time', 'evalRate', 'b01', 'avgWinTicks', 'scoreMean']
  const asCols = (ks: readonly string[]): { key: string }[] => ks.map((key) => ({ key }))

  it('rollout 视图拖 winRate：eval 视图的 evalRate 仍锚在 time 后面', () => {
    const stored = resolveColOrder(METRIC_ORDER_KEYS, rollout, METRIC_ORDER_REFS)
    const after = resolveColOrder(
      METRIC_ORDER_KEYS,
      moveColKey(stored, 'winRate', 'iter'),
      METRIC_ORDER_REFS,
    )
    expect(after[0]).toBe('winRate')
    expect(keyOf(applyColOrder(asCols(evalCols), after)).slice(0, 3)).toEqual([
      'iter',
      'time',
      'evalRate',
    ])
  })

  it('eval 视图拖 evalRate：rollout 视图的 winRate 仍锚在 time 后面', () => {
    const stored = resolveColOrder(METRIC_ORDER_KEYS, evalCols, METRIC_ORDER_REFS)
    const after = resolveColOrder(
      METRIC_ORDER_KEYS,
      moveColKey(stored, 'evalRate', 'iter'),
      METRIC_ORDER_REFS,
    )
    expect(after[0]).toBe('evalRate')
    expect(keyOf(applyColOrder(asCols(rollout), after)).slice(0, 3)).toEqual([
      'iter',
      'time',
      'winRate',
    ])
  })

  it('已存顺序是权威：拖当前视图的列不重算另一视图的键', () => {
    const stored = resolveColOrder(METRIC_ORDER_KEYS, evalCols, METRIC_ORDER_REFS)
    const moved = moveColKey(stored, 'evalRate', 'time') // 用户把 eval 胜率放到 time 前
    const after = resolveColOrder(
      METRIC_ORDER_KEYS,
      moveColKey(moved, 'winRate', 'iter'), // 之后在 rollout 视图拖 winRate
      METRIC_ORDER_REFS,
    )
    expect(after.indexOf('evalRate')).toBeLessThan(after.indexOf('time'))
  })
})

describe('METRIC_ORDER_KEYS：列顺序的键全集', () => {
  it('覆盖两个视图与逐局指标的全部列，无重复', () => {
    const keys = [...METRIC_ORDER_KEYS]
    expect(new Set(keys).size).toBe(keys.length)
    for (const k of [...ROLLOUT_ROW_KEYS, ...EVAL_ROW_KEYS]) expect(keys).toContain(k)
    for (const m of BEHAVIOR_METRICS) expect(keys).toContain(behaviorColKey(m.key))
    expect(keys.length).toBe(Object.keys(METRIC_COLS).length)
  })
})

describe('moveColKey：拖到目标列之前', () => {
  it('往后拖 ⇒ 落在目标列左侧，其余相对序不变', () => {
    expect(moveColKey(KEYS, 'iter', 'kl')).toEqual(['time', 'winRate', 'iter', 'kl'])
  })

  it('往前拖 / 原地拖', () => {
    expect(moveColKey(KEYS, 'kl', 'iter')).toEqual(['kl', 'iter', 'time', 'winRate'])
    expect(moveColKey(KEYS, 'time', 'time')).toEqual([...KEYS])
  })

  it('键缺失 ⇒ 原样（陈旧顺序/拖错目标都不制造乱序），且不改入参', () => {
    expect(moveColKey(KEYS, 'nope', 'kl')).toEqual([...KEYS])
    expect(moveColKey(KEYS, 'iter', 'nope')).toEqual([...KEYS])
  })

  it('反复拖同一列 = 幂等收敛（没有累积偏移）', () => {
    const once = moveColKey(KEYS, 'iter', 'kl')
    expect(moveColKey(once, 'iter', 'kl')).toEqual(once)
  })
})

interface Row {
  iter: number
}

const columns = KEYS.map((key) => ({ key, label: key.toUpperCase(), cell: () => key }))
const rows: Row[] = [{ iter: 2 }, { iter: 1 }]

function renderTable(props: { reorderable?: boolean }): string {
  return renderToString(
    h(DataTable<Row>, {
      columns,
      rows,
      rowKey: (r: Row) => String(r.iter),
      searchKeys: ['iter' as keyof Row],
      ...props,
    }),
  )
}

describe('DataTable：reorderable 开关', () => {
  it('开 ⇒ 每个表头都可拖 + 工具栏给出「表头可拖动」提示', () => {
    const html = renderTable({ reorderable: true })
    expect(html.match(/draggable=/g)?.length).toBe(KEYS.length)
    expect(html).toContain('表头可拖动')
    expect(html).toContain('tc-table--reorder')
  })

  it('缺省 ⇒ 表头不可拖、无提示（其它表格手感不变）', () => {
    const html = renderTable({})
    expect(html).not.toContain('draggable')
    expect(html).not.toContain('表头可拖动')
  })
})

describe('接线：两张表共用一份拖拽实现，顺序各存各的', () => {
  const read = (p: string): string => readFileSync(p, 'utf8')

  it('DataTable 与首页速览表都用 colDragProps（不各写一套 drag 事件）', () => {
    expect(read('src/web/components/DataTable.tsx')).toContain("from './col-drag'")
    const hero = read('src/web/app/panels/Hero.tsx')
    expect(hero).toContain("from '../../components/col-drag'")
    expect(hero).toContain('colDragProps(')
  })

  it('指标页开着 reorderable；顺序落在各自前缀下（指标页 tc.metrics.order / 首页 tc.hero.iters.order）', () => {
    expect(read('src/web/app/panels/MetricsTable.tsx')).toContain('reorderable')
    expect(read('src/web/components/DataTable.tsx')).toContain('.order')
    expect(read('src/web/view/legacy-keys.ts')).toContain('hero.iters.order')
  })

  it('拖拽按键全集算：指标页传 METRIC_ORDER_KEYS、首页用 resolveColOrder', () => {
    expect(read('src/web/app/panels/MetricsTable.tsx')).toContain('orderKeys={METRIC_ORDER_KEYS}')
    expect(read('src/web/app/panels/MetricsTable.tsx')).toContain('orderRefs={METRIC_ORDER_REFS}')
    expect(read('src/web/components/DataTable.tsx')).toContain('resolveColOrder(')
    const hero = read('src/web/app/panels/Hero.tsx')
    expect(hero).toContain('resolveColOrder(')
    expect(hero).toContain('METRIC_ORDER_KEYS')
    expect(hero).toContain('METRIC_ORDER_REFS')
  })

  it('首帧不读 localStorage（hydrate 一致），偏好只在 effect 里恢复', () => {
    const dt = read('src/web/components/DataTable.tsx')
    expect(dt).toContain('parseColOrder(localStorage.getItem(`${storagePrefix}.order`))')
    expect(dt).not.toContain('useState<string[] | null>(() =>')
  })
})

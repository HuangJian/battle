/**
 * web-col-order.test.ts — 列顺序偏好（用户 2026-11-10：指标表各列支持拖拽表头自定义位置）。
 *
 * 分层：src/web/view/col-order.ts（纯函数）+ components/col-drag.ts（拖拽属性工厂）
 *       + DataTable 的 `reorderable` 开关（渲染级）。
 *
 * 四个钉子：
 *  ① 解析：坏 JSON / 非字符串元素 ⇒ 「没有顺序」；重复键只留第一次；
 *  ② 应用：认识的键按存储序，模型新加的列按模型序接在后面（升级后新列不会掉队/消失）；
 *  ③ 移动：from 插到 to 之前；任一键缺失 ⇒ 原样返回（陈旧存储不许制造乱序）；
 *  ④ 接线：两张表（`/metrics` 与首页速览表）共用同一份 colDragProps，顺序各存各的键。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { DataTable } from '../src/web/components/DataTable'
import { applyColOrder, moveColKey, parseColOrder } from '../src/web/view'

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

  it('认识的键按存储序，新列按模型序接在后面', () => {
    expect(keyOf(applyColOrder(COLS, ['winRate', 'iter']))).toEqual([
      'winRate',
      'iter',
      'time',
      'kl',
    ])
  })

  it('存储里的陌生键被忽略（别表的键不会变出幽灵列，也不会挤掉本表的列）', () => {
    expect(keyOf(applyColOrder(COLS, ['bm:ticks', 'kl']))).toEqual([
      'kl',
      'iter',
      'time',
      'winRate',
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

  it('首帧不读 localStorage（hydrate 一致），偏好只在 effect 里恢复', () => {
    const dt = read('src/web/components/DataTable.tsx')
    expect(dt).toContain('parseColOrder(localStorage.getItem(`${storagePrefix}.order`))')
    expect(dt).not.toContain('useState<string[] | null>(() =>')
  })
})

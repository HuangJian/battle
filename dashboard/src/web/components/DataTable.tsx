/** DataTable.tsx — 通用数据表：表头排序（data-v 语义保留）+ 列显隐菜单 + 密度切换 +
 *  关键词过滤 + 粘性表头 + 行展开 + 空/加载态。组件内 state 自有，绝不上升（硬边界）。
 *  可选 storagePrefix 持久化到 tc.* 命名空间。
 *
 *  `columnsMenu={false}` = 显隐由调用方接管（指标页把它放进「指标表配置」弹窗）：
 *  本组件既不藏列也不写显隐存储——两处同时写一个键，弹窗里勾了列也会被旧存储重新藏起来。
 *
 *  `reorderable` = 表头可拖拽重排（用户 2026-11-10）：顺序存 `${storagePrefix}.order`，
 *  首帧恒模型顺序、mount 后恢复（SSR/hydrate 一致），写入只发生在松手那一刻。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import { applyColOrder, keywordMatch, moveColKey, parseColOrder, type SortDir } from '../view'
import { colDragProps } from './col-drag'

export interface Col<T> {
  key: string
  label: string
  align?: 'num' | 'left'
  hiddenByDefault?: boolean
  /** 缺省 = 取 row[key] 原始值（内部仍做数值优先比较）。 */
  sortValue?: (row: T) => number | string | null
  cell: (row: T) => ComponentChildren
  thTitle?: string
}

export interface DataTableSelection<T> {
  isSelected: (row: T) => boolean
  onToggleRow: (row: T) => void
  /** 表头复选框：收到当前过滤+排序后的可见行——已全选则全清，否则全选。 */
  onToggleAll: (visibleRows: T[]) => void
}

export interface DataTableProps<T> {
  columns: Col<T>[]
  rows: T[]
  rowKey: (row: T) => string
  /** 关键词过滤命中的字段名。 */
  searchKeys: Array<keyof T>
  initialSortKey?: string
  initialSortDir?: SortDir
  /** 提供则把排序/密度/列显隐持久化到 localStorage（tc.node.<view>.* 语义）。 */
  storagePrefix?: string
  /** 内置「列▾」菜单（缺省开）。调用方自己接管列显隐时传 false：本组件不再藏列、也不再写
   *  显隐存储——否则同一个键上有两个写者，弹窗里勾了列也可能被旧存储重新藏起来。 */
  columnsMenu?: boolean
  /** 表头拖拽重排（缺省关：只给真需要自定义列序的表开，不改变其它表格的手感）。
   *  开则顺序持久化到 `${storagePrefix}.order`（需要 storagePrefix 才会存）。 */
  reorderable?: boolean
  /** 行尾 ▸ 展开详情；缺省 = 无展开列。 */
  expandRender?: (row: T) => ComponentChildren
  emptyText?: string
  /** 趋势卡点选联动列高亮。 */
  highlightCol?: string | null
  ariaLabel?: string
  /** 渲染在工具栏最左段（主过滤/状态区，其他表格面板可并入自己对表格的控制）。 */
  toolbarLeft?: ComponentChildren
  /** 可选勾选列（行复选框 + 表头全选）：勾选态由调用方持有，本组件只转发事件。 */
  selection?: DataTableSelection<T>
}

/** 列显隐的默认值：`hiddenByDefault` 列在**首次**出现时隐藏；用户手点后以存储为准。
 *  存储里额外记一个默认集合签名，新增默认隐藏列时只合并一次（不会把用户已打开的列再次藏起来）。 */
function loadHidden(
  storagePrefix: string | undefined,
  columns: Array<{ key: string; hiddenByDefault?: boolean }>,
): Set<string> {
  const defaults = columns.filter((c) => c.hiddenByDefault).map((c) => c.key)
  if (!storagePrefix || typeof localStorage === 'undefined') return new Set(defaults)
  try {
    const raw = localStorage.getItem(`${storagePrefix}.hidden`)
    if (raw === null) return new Set(defaults)
    const stored = JSON.parse(raw) as unknown
    if (!Array.isArray(stored) || !stored.every((x) => typeof x === 'string')) {
      return new Set(defaults)
    }
    const sig = defaults.join(',')
    const seen = localStorage.getItem(`${storagePrefix}.hidden.defaults`)
    return new Set(seen === sig ? stored : [...stored, ...defaults])
  } catch {
    return new Set(defaults)
  }
}

function loadPref<T>(
  storagePrefix: string | undefined,
  key: string,
  dft: T,
  valid?: (v: T) => boolean,
): T {
  if (!storagePrefix || typeof localStorage === 'undefined') return dft
  try {
    const raw = localStorage.getItem(`${storagePrefix}.${key}`)
    if (raw === null) return dft
    const v = JSON.parse(raw) as T
    return v !== null && (valid ? valid(v) : true) ? v : dft
  } catch {
    return dft
  }
}

/** 共享的空集合（`columnsMenu={false}` 时用；常量避免每次 render 新建）。 */
const EMPTY_HIDDEN: ReadonlySet<string> = new Set<string>()

export function DataTable<T>(props: DataTableProps<T>) {
  const {
    columns,
    rows,
    rowKey,
    searchKeys,
    storagePrefix,
    expandRender,
    emptyText = '无数据',
    highlightCol = null,
    ariaLabel,
    toolbarLeft,
    selection,
  } = props

  const [sortKey, setSortKey] = useState<string>(props.initialSortKey ?? '')
  const [sortDir, setSortDir] = useState<SortDir>(props.initialSortDir ?? 'desc')
  const [dense, setDense] = useState<boolean>(() => loadPref(storagePrefix, 'dense', false))
  const [keyword, setKeyword] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [colMenuOpen, setColMenuOpen] = useState(false)
  const showColumnsMenu = props.columnsMenu !== false
  const [hiddenOwn, setHidden] = useState<Set<string>>(() =>
    showColumnsMenu ? loadHidden(storagePrefix, columns) : new Set<string>(),
  )
  /** 显隐被调用方接管（`columnsMenu={false}`）⇒ 本组件不藏任何列。 */
  const hidden = showColumnsMenu ? hiddenOwn : EMPTY_HIDDEN
  // ── 表头拖拽重排（用户 2026-11-10）──
  // 首帧恒模型顺序（SSR/hydrate 一致），偏好 mount 后由 effect 恢复；写入只在松手时发生
  // ——上面那个「每次 render 都写」的 effect 不碰顺序，否则首帧的 null 会把已存顺序抹掉。
  const reorderable = props.reorderable === true
  const [colOrder, setColOrder] = useState<string[] | null>(null)
  const [dragKey, setDragKey] = useState<string | null>(null)
  /** 拖完那一下的 click 不算排序（部分浏览器在 DnD 后仍补一个 click）。 */
  const dragGuard = useRef(false)
  useEffect(() => {
    if (!reorderable || !storagePrefix) return
    try {
      setColOrder(parseColOrder(localStorage.getItem(`${storagePrefix}.order`)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [reorderable, storagePrefix])
  /** 全部列（含被藏起来的）的显示顺序：拖动只改它，藏起来的列因此保住位置。 */
  const ordered = reorderable ? applyColOrder(columns, colOrder) : columns
  const onMoveCol = (from: string, to: string): void => {
    const next = moveColKey(
      ordered.map((c) => c.key),
      from,
      to,
    )
    setColOrder(next)
    try {
      if (storagePrefix) localStorage.setItem(`${storagePrefix}.order`, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }
  const hiddenDefaultsSig = columns
    .filter((c) => c.hiddenByDefault)
    .map((c) => c.key)
    .join(',')
  const menuRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const headerCheckRef = useRef<HTMLInputElement>(null)

  /** 「返回顶部」：把表格滚动容器平滑滚回顶（长表/深抽屉快速回位）。 */
  const scrollTop = (): void => {
    if (wrapRef.current) wrapRef.current.scrollTo({ top: 0, behavior: 'smooth' })
  }

  useEffect(() => {
    if (!storagePrefix || typeof localStorage === 'undefined') return
    try {
      localStorage.setItem(`${storagePrefix}.sort`, JSON.stringify({ k: sortKey, d: sortDir }))
      localStorage.setItem(`${storagePrefix}.dense`, JSON.stringify(dense))
      if (!showColumnsMenu) return
      localStorage.setItem(`${storagePrefix}.hidden`, JSON.stringify([...hidden]))
      localStorage.setItem(`${storagePrefix}.hidden.defaults`, hiddenDefaultsSig)
    } catch {
      /* 不可写不致命 */
    }
  }, [storagePrefix, sortKey, sortDir, dense, hidden, hiddenDefaultsSig, showColumnsMenu])

  useEffect(() => {
    const onDoc = (e: MouseEvent): void => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setColMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const visible = ordered.filter((c) => !hidden.has(c.key))
  const filtered = rows.filter((r) =>
    keywordMatch(r as unknown as Record<string, unknown>, searchKeys as string[], keyword),
  )

  // 表头全选框三态：checked = 可见行全选；indeterminate = 部分选中（Preact 需手动置 DOM 属性）。
  const sel =
    selection && filtered.length > 0
      ? {
          all: filtered.every((r) => selection.isSelected(r)),
          some: filtered.some((r) => selection.isSelected(r)),
        }
      : null
  useEffect(() => {
    if (headerCheckRef.current) headerCheckRef.current.indeterminate = !!sel?.some && !sel?.all
  }, [sel?.all, sel?.some])

  const sorted = (() => {
    if (rows.length <= 1 || !sortKey) return filtered
    const col = columns.find((c) => c.key === sortKey)
    if (!col) return filtered
    const mult = sortDir === 'desc' ? -1 : 1
    return [...filtered].sort((a, b) => {
      const av = col.sortValue ? col.sortValue(a) : (a as Record<string, unknown>)[sortKey]
      const bv = col.sortValue ? col.sortValue(b) : (b as Record<string, unknown>)[sortKey]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * mult
      const an = typeof av === 'string' ? Number(av) : Number.NaN
      const bn = typeof bv === 'string' ? Number(bv) : Number.NaN
      const cmp =
        !Number.isNaN(an) && !Number.isNaN(bn) ? an - bn : String(av).localeCompare(String(bv))
      return cmp * mult
    })
  })()

  const onSort = (key: string): void => {
    if (sortKey === key) setSortDir(sortDir === 'desc' ? 'asc' : 'desc')
    else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const toggleCol = (key: string): void => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="tc-dtable">
      <div
        className="tc-toolbar"
        role="toolbar"
        aria-label={ariaLabel ? `${ariaLabel} 工具栏` : undefined}
      >
        {toolbarLeft}
        <input
          type="text"
          placeholder="关键词过滤…"
          aria-label="关键词过滤"
          value={keyword}
          onInput={(e) => setKeyword((e.target as HTMLInputElement).value)}
        />
        {showColumnsMenu ? (
          <div className="tc-colmenu" ref={menuRef}>
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              aria-label="列显隐"
              onClick={() => setColMenuOpen((v) => !v)}
            >
              列▾
            </button>
            {colMenuOpen ? (
              <div className="tc-colmenu__panel" role="menu" aria-label="列显隐">
                {columns.map((c) => (
                  <label key={c.key}>
                    <input
                      type="checkbox"
                      checked={!hidden.has(c.key)}
                      onChange={() => toggleCol(c.key)}
                    />
                    <span>{c.label}</span>
                  </label>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="tc-segmented" role="group" aria-label="表格密度">
          <button
            type="button"
            className={`tc-segmented__btn${dense ? '' : ' tc-segmented__btn--on'}`}
            aria-pressed={!dense}
            title="宽松行距"
            onClick={() => setDense(false)}
          >
            舒适
          </button>
          <button
            type="button"
            className={`tc-segmented__btn${dense ? ' tc-segmented__btn--on' : ''}`}
            aria-pressed={dense}
            title="紧凑行距"
            onClick={() => setDense(true)}
          >
            紧凑
          </button>
        </div>
        <span className="tc-muted">{filtered.length} 行</span>
        {reorderable ? (
          <span className="tc-muted tc-small" title="按住表头左右拖动可调整列顺序；顺序会记住">
            表头可拖动
          </span>
        ) : null}
        <button
          type="button"
          className="tc-btn tc-btn--sm tc-toolbar__totop"
          aria-label="返回顶部"
          title="返回顶部"
          onClick={scrollTop}
        >
          ↑ 返回顶部
        </button>
      </div>
      <div className="tc-tablewrap" ref={wrapRef}>
        <table
          className={`tc-table${dense ? ' tc-table--dense' : ''}${
            reorderable ? ' tc-table--reorder' : ''
          }`}
        >
          <thead>
            <tr>
              {selection ? (
                <th aria-label="全选" className="tc-firstcol">
                  <input
                    ref={headerCheckRef}
                    type="checkbox"
                    aria-label="全选可见行"
                    checked={!!sel?.all}
                    onChange={() => selection.onToggleAll(sorted)}
                  />
                </th>
              ) : null}
              {expandRender ? <th aria-label="展开" /> : null}
              {visible.map((c, i) => (
                <th
                  key={c.key}
                  className={`${c.align === 'num' ? 'tc-num' : ''}${i === 0 ? ' tc-firstcol' : ''}${
                    sortKey === c.key ? (sortDir === 'desc' ? ' tc-sort-desc' : ' tc-sort-asc') : ''
                  }${highlightCol === c.key ? ' thhl' : ''}${
                    dragKey === c.key ? ' tc-th--dragging' : ''
                  }`}
                  title={c.thTitle}
                  onClick={() => {
                    if (dragGuard.current) return
                    onSort(c.key)
                  }}
                  {...(reorderable
                    ? colDragProps({
                        key: c.key,
                        dragging: dragKey,
                        onDragStart: (k) => {
                          dragGuard.current = true
                          setDragKey(k)
                        },
                        onDragEnd: () => {
                          setDragKey(null)
                          // 浏览器补 click 的时机不定：放过当前这一拍再恢复排序。
                          setTimeout(() => {
                            dragGuard.current = false
                          }, 0)
                        },
                        onMove: onMoveCol,
                      })
                    : {})}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={visible.length + (expandRender ? 1 : 0) + (selection ? 1 : 0)}
                  className="tc-empty"
                >
                  {emptyText}
                </td>
              </tr>
            ) : (
              sorted.map((row) => {
                const k = rowKey(row)
                const isExpanded = expandedRow === k
                return (
                  <>
                    <tr key={`${k}-main`}>
                      {selection ? (
                        <td className="tc-firstcol">
                          <input
                            type="checkbox"
                            aria-label={`选择 ${k}`}
                            checked={selection.isSelected(row)}
                            onChange={() => selection.onToggleRow(row)}
                          />
                        </td>
                      ) : null}
                      {expandRender ? (
                        <td>
                          <button
                            type="button"
                            className="tc-iconbtn"
                            aria-label={isExpanded ? '折叠行详情' : '展开行详情'}
                            onClick={() => setExpandedRow((cur) => (cur === k ? null : k))}
                          >
                            {isExpanded ? '▾' : '▸'}
                          </button>
                        </td>
                      ) : null}
                      {visible.map((c, i) => (
                        <td
                          key={c.key}
                          className={`${c.align === 'num' ? 'tc-num' : ''}${i === 0 ? ' tc-firstcol' : ''}${
                            highlightCol === c.key ? ' tdhl' : ''
                          }`}
                        >
                          {c.cell(row)}
                        </td>
                      ))}
                    </tr>
                    {isExpanded && expandRender ? (
                      <tr key={`${k}-detail`} className="tc-row-expand">
                        <td colSpan={visible.length + 1 + (selection ? 1 : 0)}>
                          {expandRender(row)}
                        </td>
                      </tr>
                    ) : null}
                  </>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** DataTable.tsx — 通用数据表：表头排序（data-v 语义保留）+ 列显隐菜单 + 密度切换 +
 *  关键词过滤 + 粘性表头 + 行展开 + 空/加载态。组件内 state 自有，绝不上升（硬边界）。
 *  可选 storagePrefix 持久化到 tc.* 命名空间。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import { keywordMatch, type SortDir } from '../view'

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
  /** 行尾 ▸ 展开详情；缺省 = 无展开列。 */
  expandRender?: (row: T) => ComponentChildren
  emptyText?: string
  /** 趋势卡点选联动列高亮。 */
  highlightCol?: string | null
  ariaLabel?: string
  /** 渲染在工具栏最左段（主过滤/状态区，其他表格面板可并入自己对表格的控制）。 */
  toolbarLeft?: ComponentChildren
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
  } = props

  const [sortKey, setSortKey] = useState<string>(props.initialSortKey ?? '')
  const [sortDir, setSortDir] = useState<SortDir>(props.initialSortDir ?? 'desc')
  const [dense, setDense] = useState<boolean>(() => loadPref(storagePrefix, 'dense', false))
  const [keyword, setKeyword] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [colMenuOpen, setColMenuOpen] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(
    () =>
      new Set(
        loadPref<string[]>(
          storagePrefix,
          'hidden',
          columns.filter((c) => c.hiddenByDefault).map((c) => c.key),
          (v) => Array.isArray(v) && v.every((x) => typeof x === 'string'),
        ),
      ),
  )
  const menuRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  /** 「返回顶部」：把表格滚动容器平滑滚回顶（长表/深抽屉快速回位）。 */
  const scrollTop = (): void => {
    if (wrapRef.current) wrapRef.current.scrollTo({ top: 0, behavior: 'smooth' })
  }

  useEffect(() => {
    if (!storagePrefix || typeof localStorage === 'undefined') return
    try {
      localStorage.setItem(`${storagePrefix}.sort`, JSON.stringify({ k: sortKey, d: sortDir }))
      localStorage.setItem(`${storagePrefix}.dense`, JSON.stringify(dense))
      localStorage.setItem(`${storagePrefix}.hidden`, JSON.stringify([...hidden]))
    } catch {
      /* 不可写不致命 */
    }
  }, [storagePrefix, sortKey, sortDir, dense, hidden])

  useEffect(() => {
    const onDoc = (e: MouseEvent): void => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setColMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const visible = columns.filter((c) => !hidden.has(c.key))
  const filtered = rows.filter((r) =>
    keywordMatch(r as unknown as Record<string, unknown>, searchKeys as string[], keyword),
  )

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
        <table className={`tc-table${dense ? ' tc-table--dense' : ''}`}>
          <thead>
            <tr>
              {expandRender ? <th aria-label="展开" /> : null}
              {visible.map((c, i) => (
                <th
                  key={c.key}
                  className={`${c.align === 'num' ? 'tc-num' : ''}${i === 0 ? ' tc-firstcol' : ''}${
                    sortKey === c.key ? (sortDir === 'desc' ? ' tc-sort-desc' : ' tc-sort-asc') : ''
                  }${highlightCol === c.key ? ' thhl' : ''}`}
                  title={c.thTitle}
                  onClick={() => onSort(c.key)}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={visible.length + (expandRender ? 1 : 0)} className="tc-empty">
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
                        <td colSpan={visible.length + 1}>{expandRender(row)}</td>
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

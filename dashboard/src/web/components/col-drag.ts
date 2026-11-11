/** col-drag.ts — 表头拖拽重排的**事件属性工厂**（用户 2026-11-10：指标表各列支持拖拽表头自定义位置）。
 *
 *  为什么是一个工厂而不是组件：两张表（`/metrics` 的 `DataTable` 与首页速览表）的 `<th>`
 *  markup 不同（一个带排序点击/高亮类，一个只有文字），但有同一套拖拽语义。共用一份事件
 *  实现，才不会出现「一处能拖、一处拖了没反应」这种分叉。
 *
 *  交互约定：
 *   · 按住表头拖到**另一列表头上松手** ⇒ 把拖的那列插到目标列之前（`moveColKey`）；
 *   · 拖动键经 `dataTransfer` 传递（跨元素可靠）；`dragging` 兜底给读不到 dataTransfer 的环境；
 *   · `onDragOver` 必须 `preventDefault()`，否则 HTML5 DnD 根本不触发 drop；
 *   · 拖动全程由调用方的 `dragging` state 上色（`.tc-th--dragging`），本模块不碰 DOM。
 */

import type { JSX } from 'preact'

/** 拖动键的 dataTransfer 类型（`text/plain` 兜底给保守实现）。 */
const DRAG_TYPE = 'text/plain'

export interface ColDragOptions {
  /** 这一列表头的列键（= drop 目标）。 */
  key: string
  /** 正在拖的列键（调用方 state；dataTransfer 读不到时用它）。 */
  dragging: string | null
  onDragStart: (key: string) => void
  onDragEnd: () => void
  /** 松手：把 `from` 挪到 `to` 之前。 */
  onMove: (from: string, to: string) => void
}

export interface ColDragProps {
  draggable: true
  onDragStart: (e: JSX.TargetedDragEvent<HTMLTableCellElement>) => void
  onDragOver: (e: JSX.TargetedDragEvent<HTMLTableCellElement>) => void
  onDrop: (e: JSX.TargetedDragEvent<HTMLTableCellElement>) => void
  onDragEnd: () => void
}

/** 一列表头的拖拽属性（直接铺到 `<th {...colDragProps(...)}>`）。 */
export function colDragProps(o: ColDragOptions): ColDragProps {
  return {
    draggable: true,
    onDragStart: (e) => {
      try {
        if (e.dataTransfer) {
          e.dataTransfer.setData(DRAG_TYPE, o.key)
          e.dataTransfer.effectAllowed = 'move'
        }
      } catch {
        /* 合成事件/受限环境没有可用的 dataTransfer：还有 o.dragging 兜底 */
      }
      o.onDragStart(o.key)
    },
    onDragOver: (e) => {
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
    },
    onDrop: (e) => {
      e.preventDefault()
      let from = ''
      try {
        from = e.dataTransfer?.getData(DRAG_TYPE) ?? ''
      } catch {
        from = ''
      }
      const src = from || o.dragging || ''
      o.onDragEnd()
      if (src && src !== o.key) o.onMove(src, o.key)
    },
    onDragEnd: () => o.onDragEnd(),
  }
}

/** sites/types.ts — 站点适配器契约（W0 只落类型；Kaggle 实现在 W2）。 */

export type SiteId = 'kaggle' | 'colab' | 'aistudio'

export interface CellRef {
  id: string
  index: number
  kind: 'code' | 'markdown' | 'raw'
}

export type CmdResult = { ok: true; detail?: string } | { ok: false; reason: string }

export interface FrameAdapter {
  readonly site: SiteId
  readonly ready: boolean
  cells(): CellRef[]
  outputText(cell: CellRef): string
  sourceText(cell: CellRef): string
  /** 当前选中/激活的 cell（没有 ⇒ null）；命令只作用于 active cell。 */
  activeCell(): CellRef | null
  ensureRendered(index: number): Promise<CellRef | null>
  select(cell: CellRef): Promise<CmdResult>
  runCell(cell: CellRef): Promise<CmdResult>
  clearCellOutput(cell: CellRef): Promise<CmdResult>
  toggleCell(cell: CellRef, part: 'input' | 'output'): Promise<CmdResult>
  /** 全部折叠：代码 / markdown 每段都保留顶部两行（原生折叠会整段藏掉，先还原）。 */
  setFoldedAll(collapsed: boolean): Promise<CmdResult>
  /** 单个 cell 折叠：`off` 在全局折叠时覆盖成展开。 */
  setFolded(cell: CellRef, collapsed: boolean): Promise<CmdResult>
  /** 命令 id 是数据不是类型（站点升级会加），失败必须可描述。 */
  command(id: string, args?: { cell?: CellRef }): Promise<CmdResult>
  scroll(to: 'top' | 'bottom'): CmdResult
  scrollOuter(to: 'top' | 'bottom'): CmdResult
  flash(cell: CellRef): void
  /** 已设「运行 cell」定时器的目标：在 cell 顶部挂醒目徽标（跨域 DOM 只有帧内能改）。 */
  setScheduledMarks(
    marks: Array<{ id?: string; label?: string; uuid?: string; index?: number }>,
  ): CmdResult
}

export interface SessionAdapter {
  readonly site: SiteId
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown'
  stopSession(): Promise<CmdResult>
  sessionId(): string
}

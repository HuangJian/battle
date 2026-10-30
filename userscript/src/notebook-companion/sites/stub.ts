/**
 * stub.ts — Colab / AI Studio 的 W5 骨架：只声明「未实现」，绝不假装成功。
 *
 * 两个站点结构未实测；等 Gate 0 级别的探测数据到位再写真实选择器。现在接入只
 * 会得到明确的 `{ok:false}`（面板红字/GM 日志），不会静默吞掉用户的清空/运行命令。
 */
import type { CellRef, CmdResult, FrameAdapter, SessionAdapter, SiteId } from './types'

const notYet = (site: SiteId): CmdResult => ({
  ok: false,
  reason: `${site} 适配器未实现（当前只支持 Kaggle；结构实测后再落地）`,
})

export class StubFrameAdapter implements FrameAdapter {
  constructor(readonly site: SiteId) {}
  readonly ready = false
  cells(): CellRef[] {
    return []
  }
  outputText(): string {
    return ''
  }
  sourceText(): string {
    return ''
  }
  async focusConfig(_markers: string[]): Promise<CmdResult> {
    return notYet(this.site)
  }
  activeCell(): CellRef | null {
    return null
  }
  async ensureRendered(_index: number): Promise<CellRef | null> {
    return null
  }
  async select(_cell: CellRef): Promise<CmdResult> {
    return notYet(this.site)
  }
  async runCell(_cell: CellRef): Promise<CmdResult> {
    return notYet(this.site)
  }
  async clearCellOutput(_cell: CellRef): Promise<CmdResult> {
    return notYet(this.site)
  }
  async toggleCell(_cell: CellRef, _part: 'input' | 'output'): Promise<CmdResult> {
    return notYet(this.site)
  }
  async setFoldedAll(_collapsed: boolean): Promise<CmdResult> {
    return notYet(this.site)
  }
  async setFolded(_cell: CellRef, _collapsed: boolean): Promise<CmdResult> {
    return notYet(this.site)
  }
  async command(_id: string, _args?: { cell?: CellRef }): Promise<CmdResult> {
    return notYet(this.site)
  }
  scroll(_to: 'top' | 'bottom'): CmdResult {
    return notYet(this.site)
  }
  scrollOuter(_to: 'top' | 'bottom'): CmdResult {
    return notYet(this.site)
  }
  flash(): void {}
  setScheduledMarks(): CmdResult {
    return notYet(this.site)
  }
}

export class StubSessionAdapter implements SessionAdapter {
  constructor(readonly site: SiteId) {}
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown' {
    return 'unknown'
  }
  sessionId(): string {
    return `${this.site}-unknown`
  }
  async stopSession(): Promise<CmdResult> {
    return notYet(this.site)
  }
}

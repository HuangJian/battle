/** PanelErrorBoundary.tsx — 每卡 render 期崩溃隔离（DS-E3）：单卡崩溃渲染占位，
 *  不白屏其它卡。零新依赖（preact class component componentDidCatch）。 */

import { Component, type ComponentChildren } from 'preact'

export interface PanelErrorBoundaryProps {
  children: ComponentChildren
}

interface PBState {
  err: string | null
}

export class PanelErrorBoundary extends Component<PanelErrorBoundaryProps, PBState> {
  state: PBState = { err: null }

  static getDerivedStateFromError(err: unknown): PBState {
    return { err: err instanceof Error ? err.message : String(err) }
  }

  componentDidCatch(err: unknown): void {
    // 保留堆栈进控制台（渲染占位会吞详情）。
    console.error('[panel error boundary]', err)
  }

  handleRetry = (): void => {
    this.setState({ err: null })
  }

  render() {
    if (this.state.err !== null) {
      return (
        <div className="tc-card__err">
          <span>该卡片加载失败：{this.state.err}</span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={this.handleRetry}>
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

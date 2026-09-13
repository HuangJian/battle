/** view-types.ts — 视图类型透传导出（单一源 web/view，本层不自定义视图类型）。 */
// ────────────────────────── 快照类型（单一源 ui/view.ts，此处仅透传导出） ──────────────────────────

export type {
  ComponentView,
  ConsoleStateView,
  LogPayload,
  MetricsView,
  ModeView,
  NodeView,
} from '../../web/view'

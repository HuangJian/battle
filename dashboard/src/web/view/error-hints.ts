/** error-hints.ts — 错误分类的展示元数据与处置提示（**纯函数**，客户端/SSR 共用）。
 *
 *  与服务端 api 层分类器的分工：那边是**规则**（哪条消息属于哪类），这边是
 *  **人话**（标签与一行处置建议）。两边都只依赖 `ErrorClassification` 枚举，类型上无法漂移；
 *  措辞不参与判定，所以放共享视图层（客户端 bundle 可直接用，无 IO）。
 */

import type { ErrorClassification } from './error-types'

/** 分组/筛选的固定顺序（顺序 = 「越靠前越像代码缺陷」；`unknown` 永不被默认折叠）。 */
export const ERROR_CLASSIFICATION_ORDER: readonly ErrorClassification[] = [
  'dependency',
  'multi-process',
  'capacity',
  'remote-failure',
  'process-lifecycle',
  'transient-network',
  'unknown',
]

/** 行内分类标签（短）。 */
export const ERROR_CLASSIFICATION_LABEL: Record<ErrorClassification, string> = {
  dependency: '依赖缺失',
  'multi-process': '多进程',
  capacity: '容量',
  'remote-failure': '远端失败',
  'process-lifecycle': '进程生命周期',
  'transient-network': '网络抖动',
  unknown: '未分类',
}

/** 默认视图是否折叠：瞬时网络与进程生命周期不是代码缺陷（plan §1.1 M5）。 */
export function isNoiseClassification(c: ErrorClassification): boolean {
  return c === 'transient-network' || c === 'process-lifecycle'
}

/** 自动撤销只看这两类（plan D3；非瞬时错误保持「消失即结束」）。 */
export function isTransientClassification(c: ErrorClassification): boolean {
  return c === 'transient-network' || c === 'process-lifecycle'
}

/** 展开 detail 时的一行处置建议；没有明确动作就返回空串（UI 不占位）。 */
export function classificationHint(c: ErrorClassification): string {
  switch (c) {
    case 'dependency':
      return '安装缺失依赖后重启 worker（numpy / libcublas）'
    case 'multi-process':
      return '检查端口占用与重复 agent 进程（/proc/net/tcp）'
    case 'remote-failure':
      return '查看对应 job 的 payload / 日志'
    case 'capacity':
      return '降低并发或扩容节点'
    default:
      return ''
  }
}

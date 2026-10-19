/** error-classify.ts — 组件错误分类器（服务端纯函数，规则表数据化）。
 *
 *  为什么集中在一处（plan §3.2）：分类会同时影响**默认视图、告警坞、处置提示**三处；
 *  规则散进各采集腿就会出现「同一条错误在日志腿是 noise、在上报腿是真问题」。
 *
 *  为什么住服务端而不是 `web/view/`：规则表是判定事实（与指纹同层），客户端只消费
 *  `classification` 字段；`web/view` 只放标签/提示（`error-hints.ts`）。
 *
 *  规则顺序 = 优先级（首命中）：`dependency` 必须先于 `transient-network`（`numpy` 不在
 *  网络词表里，但 `libcublas … not found` 的 `not found` 不能被 DNS 规则吃掉），
 *  `capacity` 必须先于 `remote-failure`（503 busy 不是 500）。新增规则 = 加一行 + 一条夹具。
 */

import type { ErrorClassification, ErrorSource } from '../../web/view'

export interface ClassifyInput {
  source: ErrorSource
  component: string
  message: string
  /** traceback / 上下文（可缺省）。只看前 4000 字符，与 `ERROR_DETAIL_MAX` 同量。 */
  detail?: string
}

interface Rule {
  re: RegExp
  classification: ErrorClassification
}

/** 规则表（顺序即优先级；正则一律 `i`）。 */
const RULES: readonly Rule[] = [
  {
    re: /ModuleNotFoundError|ImportError|No module named|libcublas|libcudnn|cannot open shared object|numpy/i,
    classification: 'dependency',
  },
  {
    re: /bootId|address already in use|EADDRINUSE|multiple\s+\S*\s*agents?|duplicate agent/i,
    classification: 'multi-process',
  },
  {
    re: /\b(?:http|status(?:Code)?)[ =:]*503\b|HTTP\/\d\.\d"\s+503\b|\b503\b[^\n]{0,24}(?:busy|unavailable|overload)|\bbusy\b|overload|queue full|no (?:available )?worker|all workers busy/i,
    classification: 'capacity',
  },
  {
    re: /internal server error|request body truncated|response send stalled|payload[^\n]{0,40}truncat|unexpected end of (?:stream|json)|HTTP\/\d\.\d"\s+5\d{2}\b|status(?:Code)?[ =:]*5\d{2}\b/i,
    classification: 'remote-failure',
  },
  {
    re: /context canceled|client disconnected|broken pipe|SIGTERM|SIGINT|signal: terminated|process exited|shutdown|restart/i,
    classification: 'process-lifecycle',
  },
  {
    re: /dial tcp|i\/o timeout|deadline exceeded|tls handshake|no such host|network is unreachable|connection reset|connection refused|incompleteread|http2[^\n]{0,20}(?:stream|goaway)|cloudflare|origin is unreachable|\b(?:521|522|523|524|530)\b/i,
    classification: 'transient-network',
  },
]

/** 一条事件 → 分类（首命中；永不为空）。`message` 是判定主输入，`detail` 兜住被截断的尾巴。 */
export function classifyError(input: ClassifyInput): ErrorClassification {
  const text = `${input.message}\n${(input.detail ?? '').slice(0, 4000)}`
  for (const rule of RULES) {
    if (rule.re.test(text)) return rule.classification
  }
  return 'unknown'
}

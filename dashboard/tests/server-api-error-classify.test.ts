/**
 * server-api-error-classify.test.ts — 分类规则表（plan/error-page-denoise-and-triage v2 §3.2）。
 *
 * 钉两件事：① §1.1/§1.2 的每个类别都有可命中的代表消息；② **顺序即优先级**
 * （`numpy` 不得判成网络抖动、`503 busy` 不得判成 500、`500 internal` 不得判成容量）。
 */

import { describe, expect, it } from 'bun:test'
import { classifyError } from '../src/server/api/error-classify'
import type { ErrorClassification, ErrorSource } from '../src/web/view'

const c = (message: string, source: ErrorSource = 'local-log', detail = ''): ErrorClassification =>
  classifyError({ source, component: 'test', message, detail })

describe('classifyError — 规则表（plan v2 §3.2）', () => {
  it('dependency：numpy / libcublas / ModuleNotFound', () => {
    expect(c("ModuleNotFoundError: No module named 'numpy'")).toBe('dependency')
    expect(c('ImportError: libcublas.so.11: cannot open shared object file: No such file')).toBe(
      'dependency',
    )
  })

  it('multi-process：bootId 变化 / 端口占用', () => {
    expect(c('bootId changed from 1699 to 1700 on port 8899')).toBe('multi-process')
    expect(c('OSError: [WinError 10048] address already in use')).toBe('multi-process')
  })

  it('capacity：503 busy，不升级成 500', () => {
    expect(c('503 Service Unavailable: busy')).toBe('capacity')
    expect(c('HTTP/1.1" 503 -')).toBe('capacity')
    expect(c('no available worker for gpu1')).toBe('capacity')
  })

  it('remote-failure：500 / 请求体截断 / 响应停滞', () => {
    expect(c('500 Internal Server Error')).toBe('remote-failure')
    expect(c('request body truncated after 4096 bytes')).toBe('remote-failure')
    expect(c('HTTP/1.1" 500 -')).toBe('remote-failure')
    expect(c('response send stalled for 30s')).toBe('remote-failure')
  })

  it('process-lifecycle：context canceled / 客户端断开', () => {
    expect(c('context canceled')).toBe('process-lifecycle')
    expect(c('client disconnected before response')).toBe('process-lifecycle')
    expect(c('signal: terminated')).toBe('process-lifecycle')
  })

  it('transient-network：隧道/DNS/TCP/http2/边缘码', () => {
    expect(c('dial tcp 127.0.0.1:8899: connectex: No connection could be made')).toBe(
      'transient-network',
    )
    expect(c('dial tcp: lookup example.com: no such host')).toBe('transient-network')
    expect(c('IncompleteRead(0 bytes read)')).toBe('transient-network')
    expect(c('http2: stream closed')).toBe('transient-network')
    expect(c('cloudflared: origin is unreachable (530)')).toBe('transient-network')
    expect(c('read tcp 10.0.0.2:5000: connection reset by peer')).toBe('transient-network')
  })

  it('unknown：真正的训练错误不被误分类', () => {
    expect(c('RuntimeError: 缺 BC 参考')).toBe('unknown')
    expect(c('ValueError: bad shape (3, 4)')).toBe('unknown')
  })

  it('优先级：dependency 先于网络、capacity 先于 remote-failure', () => {
    expect(c('ModuleNotFoundError: numpy; retrying after i/o timeout')).toBe('dependency')
    expect(c('503 busy (upstream 500 available)')).toBe('capacity')
    expect(c('500 internal server error')).toBe('remote-failure')
  })

  it('detail 兜底：message 被截断时仍能命中尾巴', () => {
    expect(c('worker failed', 'hub-job', 'Traceback…\nModuleNotFoundError: numpy')).toBe(
      'dependency',
    )
  })
})

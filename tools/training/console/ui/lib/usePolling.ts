/** usePolling.ts — setTimeout 链式轮询（不用 setInterval：上一次响应回来后才计下一次，
 *  天然防重叠与排队；GLM-U1）。enabled=false 即暂停；翻 true 时 effect 重跑 → 立即补拉一次。
 *
 *  生效优先级（GLM-E6）：
 *    hard stop（不发请求）：dirty(L2) > visibility(后台 tab) > 用户点"暂停" > 卡折叠
 *    节奏类（仍会发）：单卡覆盖 tc.interval.<card>（仅独立数据源）> 全局节奏
 */

import { useEffect, useRef } from 'preact/hooks'

export interface PollOptions {
  /** 是否允许发请求（false = 链停止；翻 true 时 effect 重跑 → 立即补拉）。 */
  enabled: boolean
  /** 轮询间隔（秒），变化立即生效而不断链。 */
  intervalSec: number
  fetch: () => Promise<void>
  /** 拉取失败回调（卡内错误处理），不抛。 */
  onError?: (err: unknown) => void
}

export function usePolling({ enabled, intervalSec, fetch, onError }: PollOptions) {
  const fetchRef = useRef(fetch)
  const onErrorRef = useRef(onError)
  const intervalRef = useRef(intervalSec)
  fetchRef.current = fetch
  onErrorRef.current = onError

  useEffect(() => {
    intervalRef.current = intervalSec
  }, [intervalSec])

  useEffect(() => {
    if (!enabled) return
    const abort = { done: false }
    let timer: ReturnType<typeof setTimeout> | null = null
    const schedule = (delayMs: number): void => {
      if (abort.done) return
      timer = setTimeout(() => void loop(), delayMs)
    }
    const loop = async (): Promise<void> => {
      try {
        await fetchRef.current()
      } catch (e) {
        onErrorRef.current?.(e)
      }
      schedule(Math.max(100, intervalRef.current * 1000))
    }
    schedule(0) // 立即首拉（不阻塞首帧渲染；false→true 恢复时同样触发）
    return () => {
      abort.done = true
      if (timer) clearTimeout(timer)
    }
  }, [enabled])
}

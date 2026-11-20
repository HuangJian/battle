/** useHomeCompare.ts — 首页走势图多课对比的数据拉取（显式序列键 + 账本指纹门闩）。
 *
 *  节奏跟顶栏全局刷新（`refreshSec`），不新增第二个开关。两条纪律与比较弹窗同源：
 *   · 请求身份（课程 + 键序）或账本指纹没变 ⇒ 不 setState（否则每拍重绘把 hover 准星清掉）；
 *   · 自增序号丢弃过期响应（快切数据源/对比课时，旧请求可能后到）。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import { homeCompareRequestKey, shouldApplyCompareData, type TrendSeriesView } from '../../view'
import { fetchTrendSeries } from './api-client'
import { usePolling } from './usePolling'

export interface HomeCompareFetchState {
  data: TrendSeriesView | null
  error: string | null
}

export function useHomeCompare(opts: {
  enabled: boolean
  courses: string[]
  keys: string[]
  refreshSec: number
}): HomeCompareFetchState {
  const [data, setData] = useState<TrendSeriesView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seqRef = useRef(0)
  const appliedRef = useRef<{ requestKey: string; fingerprint: string } | null>(null)
  const optsRef = useRef(opts)
  optsRef.current = opts
  const requestKey = homeCompareRequestKey(opts.courses, opts.keys)

  const load = async (): Promise<void> => {
    const { courses, keys } = optsRef.current
    if (courses.length === 0 || keys.length === 0) {
      appliedRef.current = null
      setData(null)
      setError(null)
      return
    }
    const my = ++seqRef.current
    try {
      const next = await fetchTrendSeries({ courses, keys, from: null, to: null })
      if (my !== seqRef.current) return // 过期响应：丢
      const meta = {
        requestKey: homeCompareRequestKey(courses, keys),
        fingerprint: next.fingerprint,
      }
      if (!shouldApplyCompareData(appliedRef.current, meta)) return
      appliedRef.current = meta
      setData(next)
      setError(null)
    } catch (e) {
      if (my !== seqRef.current) return
      setError(e instanceof Error ? e.message : String(e)) // 保留上一帧数据（不清图）
    }
  }
  const loadRef = useRef(load)
  loadRef.current = load

  usePolling({
    enabled: opts.enabled,
    intervalSec: opts.refreshSec,
    fetch: () => loadRef.current(),
  })

  // 开关/请求身份变化 ⇒ 立即重拉（不等下一拍）；关掉时清数据并作废在途响应。
  useEffect(() => {
    if (!opts.enabled) {
      appliedRef.current = null
      seqRef.current++
      setData(null)
      setError(null)
      return
    }
    void loadRef.current()
  }, [opts.enabled, requestKey])

  return { data, error }
}

/** ReplayExportModal.tsx — 「导出 replay」弹窗：最新 in-loop eval 逐局列表 +
 *  勾选 / 批量选择 + 确定性重放导出（POST evalReplays → 轮询 job → tar.gz 下载）。
 *
 *  数据源 = GET /api/evalGames（同一 (iter, wver) 的全部 event=eval 行，与指标表
 *  eval 视图同口径）；导出后端 = rl/eval_replays_once.py（冻结权重重放，逐局对账）。
 *  Esc / 遮罩关闭；勾选态本地持有，不持久化。 */

import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { EVAL_GAME_CLASS_LABEL } from '../../../ui/view'
import type { EvalGameRow, EvalGamesView } from '../../../ui/view'
import {
  canPickDirectory,
  fetchEvalGames,
  fetchEvalReplayFile,
  fetchEvalReplayJob,
  pickReplayDirectory,
  postAction,
  type ReplayDirHandle,
} from '../lib/api-client'

export interface ReplayExportModalProps {
  open: boolean
  course: string
  readOnly?: boolean
  onClose: () => void
}

const gameKey = (r: EvalGameRow): string => `${r.stage}:${r.seed}`

/** 表头批量选择按钮：匹配集全选中 = 按下态（再点 = 取消该组勾选）。 */
function BatchBtn({
  label,
  title,
  on,
  onClick,
}: {
  label: string
  title?: string
  on: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`tc-segmented__btn${on ? ' tc-segmented__btn--on' : ''}`}
      aria-pressed={on}
      title={title}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

export function ReplayExportModal({ open, course, readOnly, onClose }: ReplayExportModalProps) {
  const [view, setView] = useState<EvalGamesView | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [phase, setPhase] = useState<'idle' | 'exporting' | 'done'>('idle')
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  const autoDelivered = useRef(false)
  const dirRef = useRef<ReplayDirHandle | null>(null)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  // 打开时拉取逐局视图；恢复进行中的导出（服务端 busy 是真相）。
  useEffect(() => {
    if (!open) return
    setView(null)
    setLoadErr(null)
    setSelected(new Set())
    setPhase('idle')
    setStatusMsg(null)
    autoDelivered.current = false
    dirRef.current = null
    void (async () => {
      try {
        const v = await fetchEvalGames(course)
        if (!aliveRef.current) return
        setView(v)
        const job = await fetchEvalReplayJob(course)
        if (!aliveRef.current) return
        if (job.running) setPhase('exporting')
        else if (job.manifest) {
          setPhase('done')
          if (job.manifest.ok) autoDelivered.current = true // 上轮产物不自动重写
          else
            setStatusMsg(
              `上一次导出未产出文件（${job.manifest.errors.length} 局失败）——重试或检查日志`,
            )
        }
      } catch (e) {
        if (aliveRef.current) setLoadErr(String(e))
      }
    })()
  }, [open, course])

  /** 逐局交付：有目录句柄 → 逐文件写入指定目录；无（选择器不可用）→ 逐文件浏览器下载。 */
  const deliver = async (files: Array<{ file: string }>): Promise<void> => {
    const h = dirRef.current
    if (h) {
      let written = 0
      for (const f of files) {
        const blob = await fetchEvalReplayFile(course, f.file)
        const fh = await h.getFileHandle(f.file, { create: true })
        const w = await fh.createWritable()
        await w.write(blob)
        await w.close()
        written++
        if (!aliveRef.current) return
      }
      setStatusMsg(`已写入 ${written} 个 .replay 到目录「${h.name}」`)
      return
    }
    let n = 0
    for (const f of files) {
      const blob = await fetchEvalReplayFile(course, f.file)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = f.file
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 5000)
      n++
      if (!aliveRef.current) return
      await new Promise((res) => setTimeout(res, 300)) // 间隔防浏览器多文件下载拦截
    }
    setStatusMsg(`已触发 ${n} 个 .replay 下载（浏览器下载目录）`)
  }

  // 导出中：2s 轮询任务态；结束（busy 释放）→ 展示结果 + 首次逐文件交付。
  useEffect(() => {
    if (!open || phase !== 'exporting' || !course) return
    let stop = false
    const tick = (): void => {
      void (async () => {
        try {
          const job = await fetchEvalReplayJob(course)
          if (stop || !aliveRef.current) return
          if (job.running) return
          setPhase('done')
          const m = job.manifest
          if (!m) {
            setStatusMsg('导出进程已退出但无产物清单——看日志尾')
            return
          }
          if (m.ok && m.files.length > 0) {
            setStatusMsg(
              `导出完成：${m.files.length}/${m.requested} 局（${m.sec}s）` +
                (m.errors.length > 0 ? ` · ${m.errors.length} 局失败` : '') +
                (m.mismatches.length > 0 ? ` · ${m.mismatches.length} 局对账不一致` : ''),
            )
            if (!autoDelivered.current) {
              autoDelivered.current = true
              await deliver(m.files)
            }
          } else {
            setStatusMsg(`导出失败（0 局产出）：${m.errors[0]?.error ?? '看日志尾'}`)
          }
        } catch {
          /* 轮询失败下轮再试 */
        }
      })()
    }
    const t = setInterval(tick, 2000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [open, phase, course])

  // Esc 关闭（App 全局 Esc 关弹窗/抽屉，不覆盖本弹窗）。
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const rows = view?.rows ?? []
  const counts = useMemo(() => {
    const c = { win: 0, fail: 0, timeout: 0 }
    for (const r of rows) c[r.cls]++
    return c
  }, [rows])
  const killValues = useMemo(
    () => [...new Set(rows.map((r) => r.kills))].sort((a, b) => a - b),
    [rows],
  )

  const matching = (pred: (r: EvalGameRow) => boolean): EvalGameRow[] => rows.filter(pred)
  /** 批量 toggle：匹配集全选中 → 取消该组；否则整组加入。 */
  const toggleMatch = (pred: (r: EvalGameRow) => boolean): void => {
    const hit = matching(pred)
    const allOn = hit.length > 0 && hit.every((r) => selected.has(gameKey(r)))
    setSelected((prev) => {
      const next = new Set(prev)
      for (const r of hit) {
        if (allOn) next.delete(gameKey(r))
        else next.add(gameKey(r))
      }
      return next
    })
  }
  const groupOn = (pred: (r: EvalGameRow) => boolean): boolean => {
    const hit = matching(pred)
    return hit.length > 0 && hit.every((r) => selected.has(gameKey(r)))
  }

  const onExport = (): void => {
    if (!view || !course || readOnly) return
    const games = rows
      .filter((r) => selected.has(gameKey(r)))
      .map((r) => ({ stage: r.stage, seed: r.seed }))
    if (games.length === 0) return
    void (async () => {
      // 目录选择先行：写盘目标在导出开始前确定（完成后无需人守候）。
      if (canPickDirectory()) {
        const h = await pickReplayDirectory()
        if (!aliveRef.current) return
        if (!h) {
          setStatusMsg('未选择目录——已取消导出')
          return
        }
        dirRef.current = h
      } else {
        dirRef.current = null // Firefox/Safari：退化为逐文件浏览器下载
      }
      setStatusMsg(`导出启动中（${games.length} 局）…`)
      const r = await postAction('evalReplays', {
        course,
        iter: view.iter,
        wver: view.wver,
        games,
      })
      if (!aliveRef.current) return
      if (r.ok) {
        setPhase('exporting')
      } else {
        setStatusMsg(r.message)
      }
    })()
  }

  const columns: Col<EvalGameRow>[] = [
    {
      key: 'cls',
      label: '类型',
      align: 'left',
      sortValue: (r) => ({ win: 0, fail: 1, timeout: 2 })[r.cls],
      cell: (r) => (
        <>
          <b className={`tc-replay-cls--${r.cls}`}>{EVAL_GAME_CLASS_LABEL[r.cls]}</b>
          {r.cleared && r.cls !== 'win' ? (
            <span className="tc-pill tc-pill--note" title="敌人已全歼，仅 BONUS TIME 窗口被 max_ticks 截断">
              全歼
            </span>
          ) : null}
        </>
      ),
    },
    {
      key: 'stage',
      label: '关卡',
      align: 'left',
      sortValue: (r) => r.stage,
      cell: (r) => (
        <span title={`stage ${r.stage}`}>
          {r.stageName || `s${r.stage}`}
          <span className="tc-muted"> · {r.stage}</span>
        </span>
      ),
    },
    { key: 'seed', label: 'seed', align: 'num', cell: (r) => r.seed },
    {
      key: 'ticks',
      label: '耗时',
      align: 'num',
      thTitle: '整局 ticks（60/s）',
      cell: (r) => r.ticks,
    },
    { key: 'kills', label: '击杀', align: 'num', thTitle: '击杀数 / 每局总敌数见关卡列', cell: (r) => r.kills },
    {
      key: 'dmgPerKill',
      label: '承伤/杀',
      align: 'num',
      thTitle: '该局承伤 ÷ 击杀（击杀 0 = 无定义）；括号内为承伤原值',
      sortValue: (r) => r.dmgPerKill,
      cell: (r) =>
        r.dmgPerKill != null ? (
          <span title={`承伤 ${r.dmgTaken}`}>
            {r.dmgPerKill.toFixed(1)}
            <span className="tc-muted tc-small"> ({r.dmgTaken})</span>
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      key: 'residualHp',
      label: '残血',
      align: 'num',
      thTitle: '胜局剩余 hp（剩余命每命计满额）；败局不定义',
      sortValue: (r) => r.residualHp,
      cell: (r) => (r.residualHp != null ? r.residualHp : <span className="tc-muted">-</span>),
    },
    { key: 'pu', label: '道具', align: 'num', thTitle: '拾取道具数', cell: (r) => r.pu },
    {
      key: 'score',
      label: '得分',
      align: 'num',
      sortValue: (r) => r.score,
      cell: (r) => (r.score != null ? r.score.toFixed(4) : <span className="tc-muted">-</span>),
    },
    { key: 'node', label: '节点', align: 'left', hiddenByDefault: true, cell: (r) => r.node },
    {
      key: 'time',
      label: '落账时间',
      align: 'left',
      hiddenByDefault: true,
      cell: (r) => <span className="tc-muted tc-small">{r.time}</span>,
    },
  ]

  if (!open) return null
  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div className="tc-modal tc-modal--wide" role="dialog" aria-label="导出 replay" onClick={(e) => e.stopPropagation()}>
        <div className="tc-replay-modal__hd">
          <h3>
            导出 replay{' '}
            {view?.available ? (
              <span className="tc-muted tc-small">
                · it{view.iter} 干净评估 · {view.games} 局 {view.wins} 胜 · wver{' '}
                {view.wver.slice(0, 12)}…
              </span>
            ) : null}
          </h3>
          <span className="tc-muted tc-small" title="导出 = 用该轮冻结权重确定性重放整局并录制输入；完成后每局一个 .replay 写入你指定的目录（无目录选择器的浏览器退化为逐文件下载）">
            确定性重放 · 每局一个 .replay 文件
          </span>
        </div>
        {loadErr ? (
          <p className="tc-banner tc-banner--err">加载失败：{loadErr}</p>
        ) : !view ? (
          <p className="tc-muted">加载中…</p>
        ) : !view.available ? (
          <p className="tc-muted">该课程暂无 eval 评估记录</p>
        ) : (
          <DataTable<EvalGameRow>
            columns={columns}
            rows={rows}
            rowKey={gameKey}
            searchKeys={['stageName', 'node', 'time']}
            initialSortKey="cls"
            initialSortDir="asc"
            ariaLabel="eval 逐局列表"
            emptyText="该轮无逐局记录"
            selection={{
              isSelected: (r) => selected.has(gameKey(r)),
              onToggleRow: (r) =>
                setSelected((prev) => {
                  const next = new Set(prev)
                  if (next.has(gameKey(r))) next.delete(gameKey(r))
                  else next.add(gameKey(r))
                  return next
                }),
              onToggleAll: (visible) => {
                const allOn = visible.every((r) => selected.has(gameKey(r)))
                setSelected((prev) => {
                  const next = new Set(prev)
                  for (const r of visible) {
                    if (allOn) next.delete(gameKey(r))
                    else next.add(gameKey(r))
                  }
                  return next
                })
              },
            }}
            toolbarLeft={
              <span className="tc-replay-batch" role="group" aria-label="批量选择">
                <span className="tc-muted tc-small">已选 {selected.size}</span>
                <BatchBtn
                  label="全部"
                  on={rows.length > 0 && selected.size === rows.length}
                  onClick={() =>
                    setSelected((prev) =>
                      prev.size === rows.length ? new Set() : new Set(rows.map(gameKey)),
                    )
                  }
                />
                <BatchBtn
                  label={`胜利 ${counts.win}`}
                  on={groupOn((r) => r.cls === 'win')}
                  onClick={() => toggleMatch((r) => r.cls === 'win')}
                />
                <BatchBtn
                  label={`失败 ${counts.fail}`}
                  on={groupOn((r) => r.cls === 'fail')}
                  onClick={() => toggleMatch((r) => r.cls === 'fail')}
                />
                <BatchBtn
                  label={`超时 ${counts.timeout}`}
                  on={groupOn((r) => r.cls === 'timeout')}
                  onClick={() => toggleMatch((r) => r.cls === 'timeout')}
                />
                <span className="tc-replay-batch__sep" />
                {killValues.map((k) => (
                  <BatchBtn
                    key={`k${k}`}
                    label={`kills=${k}`}
                    on={groupOn((r) => r.kills === k)}
                    onClick={() => toggleMatch((r) => r.kills === k)}
                  />
                ))}
                <BatchBtn label="清空" on={false} onClick={() => setSelected(new Set())} />
              </span>
            }
          />
        )}
        <div className="tc-modal__foot">
          {phase === 'exporting' ? (
            <span className="tc-muted tc-small" role="status">
              重放导出中…（每局为一次完整确定性仿真，可关闭弹窗，完成后回来保存）
            </span>
          ) : (
            <span className="tc-muted tc-small">{statusMsg}</span>
          )}
          <span className="sp" />
          {phase === 'done' && statusMsg ? (
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              disabled={readOnly}
              title="把最近一次导出的 .replay 重新写入目录（或逐文件下载）"
              onClick={() => {
                void (async () => {
                  if (canPickDirectory()) {
                    const h = await pickReplayDirectory()
                    if (!h) return
                    dirRef.current = h
                  }
                  const job = await fetchEvalReplayJob(course)
                  if (job.manifest && job.manifest.files.length > 0) {
                    autoDelivered.current = true // 手动触发不重复
                    await deliver(job.manifest.files)
                  }
                })()
              }}
            >
              重新保存
            </button>
          ) : null}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={readOnly || selected.size === 0 || phase === 'exporting' || !view?.available}
            title={
              readOnly
                ? '局域网只读：导出仅在本机 localhost 生效'
                : `确定性重放所选 ${selected.size} 局并录制 .replay`
            }
            onClick={onExport}
          >
            {phase === 'exporting' ? '导出中…' : `导出所选 (${selected.size})`}
          </button>
          <button type="button" className="tc-btn tc-btn--sm" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}

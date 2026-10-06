/** ReplayExportModal.tsx — 「导出 replay」弹窗：**任意 eval 轮**逐局列表 +
 *  勾选 / 批量选择 + 确定性重放导出（POST evalReplays → 轮询 job → 逐文件落盘）。
 *
 *  数据源 = GET /api/evalRounds（选择器，summary-only）+ GET /api/evalGames?iter=N
 *  （同一 (iter, wver) 的全部 event=eval 行，与指标表 eval 视图同口径）；默认停在最新轮
 *  （= 降序首项，今天的行为逐字不变）。导出后端 = worker/eval_replays_once.py（冻结权重重放，逐局对账）。
 *
 *  归因判据（§3.6，本文件的纪律核心）：`manifest.iter === 所选轮` 才算「本次结果」——否则一律当
 *  「上一次产物」（异轮 / 无清单 / 失败都不自动交付）；服务端受理时已删旧 manifest（route.ts）。
 *  Esc / 遮罩关闭；勾选态本地持有，不持久化。 */

import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { DataTable, type Col } from '../../components/DataTable'
import { EVAL_GAME_CLASS_LABEL } from '../../view'
import type { EvalGameRow, EvalGamesView, EvalReplayManifest, EvalRoundOption } from '../../view'
import {
  canPickDirectory,
  fetchEvalGames,
  fetchEvalReplayFile,
  fetchEvalReplayJob,
  fetchEvalRounds,
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

/** 轮选择器禁用（§3.7）：导出中禁选（busy 是全局单键，切轮只会换来「重导吃 409」）；无轮可禁。 */
export function pickerDisabled(phase: 'idle' | 'exporting' | 'done', roundCount: number): boolean {
  return phase === 'exporting' || roundCount === 0
}

/** 视图不可用时的空文案（§3.2）：显式选了某轮 ⇒ 说「该轮」；没选 ⇒ 说「该课程」。 */
export function emptyViewText(selectedIter: number | null): string {
  return selectedIter != null ? '该轮无评估记录' : '该课程暂无 eval 评估记录'
}

/** 轮选择器文案：it0 = 基线轮；winRate 缺失显式 '-'; 回填读数（无本轮逐局行）标在末尾。 */
export function roundLabel(r: EvalRoundOption): string {
  const wr = r.winRate != null ? `${(r.winRate * 100).toFixed(0)}%` : '-'
  const head = r.iter <= 0 ? '基线 it0（bc 权重）' : `it${r.iter}`
  const games = r.games != null ? `${r.games} 局` : '局数未知'
  const reuse = r.reusedWver ? ' · 回填读数' : ''
  return `${head} · ${wr} · ${games}${r.time ? ` · ${r.time}` : ''}${reuse}`
}

/** 任务态 → 弹窗该说什么（§3.6 归因判据）。**只有 `kind === 'done'` 允许把 files 交付给用户**：
 *  其余（无清单 / 异轮 / 失败）一律当「上一次产物」——自动交付 = 把别的次的文件写成这次的成果。 */
export type JobOutcome = {
  kind: 'done' | 'failed' | 'other-round' | 'no-manifest'
  message: string
  files: Array<{ stage: number; seed: number; file: string }>
}

export function jobOutcome(m: EvalReplayManifest | null, selectedIter: number | null): JobOutcome {
  if (!m) {
    return { kind: 'no-manifest', message: '导出进程已退出但无产物清单——看日志尾', files: [] }
  }
  if (m.iter !== selectedIter) {
    const cur = selectedIter == null ? '（未选轮）' : `it${selectedIter}`
    return {
      kind: 'other-round',
      message:
        `最近一次导出是 it${m.iter}，与当前所选${cur}不是同一轮——不自动交付` +
        (m.ok ? '；如需该产物请切到该轮再「重新保存」' : ''),
      files: [],
    }
  }
  if (!m.ok || m.files.length === 0) {
    return {
      kind: 'failed',
      message: `导出失败：${m.failReason ?? m.errors[0]?.error ?? '0 局产出——看日志尾'}`,
      files: [],
    }
  }
  const detail =
    (m.errors.length > 0 ? ` · ${m.errors.length} 局失败` : '') +
    (m.mismatches.length > 0 ? ` · ${m.mismatches.length} 局对账不一致` : '')
  return {
    kind: 'done',
    message: `导出完成：${m.files.length}/${m.requested} 局（${m.sec}s）${detail}`,
    files: m.files,
  }
}

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
  // F4 闭合（串话归因，弹窗侧）：任务态判据只认本次受理后落盘的 manifest——manifest.iter === 所选轮 才算本次结果。
  // 旧弹窗用法（此接口做轮选择）已废除——轮选择走 GET /api/evalRounds。
  const [view, setView] = useState<EvalGamesView | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [phase, setPhase] = useState<'idle' | 'exporting' | 'done'>('idle')
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  const [rounds, setRounds] = useState<EvalRoundOption[]>([])
  const [selectedIter, setSelectedIter] = useState<number | null>(null)
  const autoDelivered = useRef(false)
  const dirRef = useRef<ReplayDirHandle | null>(null)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  // 打开时：先拉轮列表（summary-only），默认停在最新轮（= 降序首项，今天的行为），再拉该轮逐局视图；
  // 恢复进行中的导出（服务端 busy 是真相）。已完成的任务态按 §3.6 归因：manifest.iter 必须等于所选轮。
  useEffect(() => {
    if (!open) return
    setView(null)
    setRounds([])
    setSelectedIter(null)
    setLoadErr(null)
    setSelected(new Set())
    setPhase('idle')
    setStatusMsg(null)
    autoDelivered.current = false
    dirRef.current = null
    void (async () => {
      try {
        const rs = await fetchEvalRounds(course)
        if (!aliveRef.current) return
        setRounds(rs.rounds)
        const dft = rs.rounds.length > 0 ? rs.rounds[0].iter : undefined
        setSelectedIter(dft ?? null)
        const v = await fetchEvalGames(course, dft)
        if (!aliveRef.current) return
        setView(v)
        const job = await fetchEvalReplayJob(course)
        if (!aliveRef.current) return
        if (job.running) setPhase('exporting')
        else if (job.manifest) {
          // 上轮产物**不自动重写**；同轮才提示「重新保存」，异轮明说是哪一轮。
          autoDelivered.current = true
          setPhase('done')
          setStatusMsg(jobOutcome(job.manifest, dft ?? null).message)
        }
      } catch (e) {
        if (aliveRef.current) setLoadErr(String(e))
      }
    })()
  }, [open, course])

  /** 切轮（§3.7）：重拉该轮视图，并复位**这次导出任务**的全部状态（勾选 / 状态行 / phase / 目录句柄）。
   *  导出中禁用（busy 是全局单键，切轮只会带来「重导吃 409」的困惑）。 */
  const pickRound = (it: number): void => {
    if (phase === 'exporting') return
    setSelectedIter(it)
    setSelected(new Set())
    setStatusMsg(null)
    setPhase('idle')
    autoDelivered.current = false
    dirRef.current = null
    setLoadErr(null)
    setView(null)
    void (async () => {
      try {
        const v = await fetchEvalGames(course, it)
        if (aliveRef.current) setView(v)
      } catch (e) {
        if (aliveRef.current) setLoadErr(String(e))
      }
    })()
  }

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

  // 导出中：2s 轮询任务态；结束（busy 释放）→ 按归因判据（§3.6）展示结果 + 首次逐文件交付。
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
          const o = jobOutcome(job.manifest, selectedIter)
          setStatusMsg(o.message)
          if (o.kind === 'done' && !autoDelivered.current) {
            autoDelivered.current = true
            await deliver(o.files)
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
  }, [open, phase, course, selectedIter])

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
  const selRound = rounds.find((r) => r.iter === selectedIter) ?? null
  /** 回填读数轮（summary 有 games、本轮逐局行一条都没有）：明说「为什么表是空的」。 */
  const reusedNote =
    view?.available && rows.length === 0 && (selRound?.games ?? 0) > 0
      ? `该轮摘要是回填读数（${selRound?.games} 局来自其它轮）：本轮的逐局行不在账本里，无法勾选导出。`
      : null
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
      // F4 闭合（串话归因，触发点）：POST /api/replayExport 受理成功 ⇒ 服务端已清旧 manifest，
      // 保证本次 manifest 是唯一的「盘上有 manifest」来源（manifest.iter === 所选轮）。
      // 旧弹窗用法（此接口做轮选择）已废除——轮选择走 GET /api/evalRounds。
      if (selectedIter != null && view.iter !== selectedIter) {
        // 视图与所选轮不同源（理论上到不了）：宁可拒绝，也不拿别的轮的 wver 去重放。
        setStatusMsg('所选轮的数据已刷新——请重开弹窗再试')
        return
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
        autoDelivered.current = false // 新任务：上一次的「已交付」标记不得继承
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
            <span
              className="tc-pill tc-pill--note"
              title="敌人已全歼，仅 BONUS TIME 窗口被 max_ticks 截断"
            >
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
    {
      key: 'kills',
      label: '击杀',
      align: 'num',
      thTitle: '击杀数 / 每局总敌数见关卡列',
      cell: (r) => r.kills,
    },
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
      <div
        className="tc-modal tc-modal--wide"
        role="dialog"
        aria-label="导出 replay"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tc-replay-modal__hd">
          <h3>
            导出 replay{' '}
            <select
              className="tc-sel"
              aria-label="选择评估轮"
              title="选哪一轮，就用哪一轮的冻结权重确定性重放（默认最新轮；合成项带「回填读数」= 本轮无逐局行）"
              disabled={pickerDisabled(phase, rounds.length)}
              value={selectedIter == null ? '' : String(selectedIter)}
              onChange={(e) => pickRound(Number((e.target as HTMLSelectElement).value))}
            >
              {rounds.length === 0 ? <option value="">暂无评估轮</option> : null}
              {rounds.map((r) => (
                <option key={r.iter} value={String(r.iter)}>
                  {roundLabel(r)}
                </option>
              ))}
            </select>{' '}
            {view?.available ? (
              <span className="tc-muted tc-small">
                · it{view.iter} 干净评估 · {view.games} 局 {view.wins} 胜 · wver{' '}
                {view.wver.slice(0, 12)}…
              </span>
            ) : null}
          </h3>
          <span
            className="tc-muted tc-small"
            title="导出 = 用该轮冻结权重确定性重放整局并录制输入；完成后每局一个 .replay 写入你指定的目录（无目录选择器的浏览器退化为逐文件下载）"
          >
            确定性重放 · 每局一个 .replay 文件
          </span>
        </div>
        {loadErr ? (
          <p className="tc-banner tc-banner--err">加载失败：{loadErr}</p>
        ) : !view ? (
          <p className="tc-muted">加载中…</p>
        ) : !view.available ? (
          <p className="tc-muted">{emptyViewText(selectedIter)}</p>
        ) : (
          <>
            {reusedNote ? <p className="tc-banner tc-banner--muted">{reusedNote}</p> : null}
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
          </>
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
              title="把最近一次导出（须是当前所选轮）的 .replay 重新写入目录（或逐文件下载）"
              onClick={() => {
                void (async () => {
                  if (canPickDirectory()) {
                    const h = await pickReplayDirectory()
                    if (!h) return
                    dirRef.current = h
                  }
                  const job = await fetchEvalReplayJob(course)
                  const o = jobOutcome(job.manifest, selectedIter)
                  if (o.kind !== 'done') {
                    setStatusMsg(o.message) // 异轮 / 失败 / 无清单：不交付别的次的产物
                    return
                  }
                  autoDelivered.current = true // 手动触发不重复
                  await deliver(o.files)
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

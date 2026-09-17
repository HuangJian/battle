/** WirePanel.tsx — 「传输」视图：远端 PPO 每轮**实测**字节/秒数 + M1 隧道 A/B 对比。
 *
 *  为什么要有这一屏（`plan/remote-wire-remediation.plan.md` §2.1）：在这一套计量上线前，
 *  「传输慢」只能靠「墙钟 − 各阶段秒」推断，A/B 无法归因；字节数差 4× 也只能靠猜。
 *  这里把三件事摆在一起看：
 *    ① 本轮账（up/down 实测字节、up/pack 秒、blob 未命中、当轮开关取值）；
 *    ② 最近若干轮的走势表（判「是持续慢还是某一轮退化」——单点数字看不出趋势）；
 *    ③ 隧道 A/B 探针的 p50/p90（判 http2 vs quic 的腿间差，且新→旧列出以看连跑是否退化）。
 *
 *  数据来源两条、都只读：`stateView.metrics.iters[].wire`（账本）与 `stateView.tunnelAb`
 *  （`tmp/tunnel-ab-*.json`）。两者缺一律显空态 + 重跑命令，绝不编造 0（0 在趋势里
 *  是「真的没传字节」的意思，虚假零点会误导）。
 *
 *  口径提醒（写在 UI 里，不指望操作员记得）：push 模式 `wire.downBytes` 为 null，
 *  下行看 worker 侧 `result_bytes`；pull 模式才是 hub 实测。
 */

import {
  fmtBytes,
  fmtRel,
  fmtTs,
  type ConsoleStateView,
  type IterRow,
  type IterWire,
  type TunnelAbRun,
} from '../../view'

export interface WirePanelProps {
  stateView: ConsoleStateView | null
  course: string
}

/** 展示用：保留 2 位小数秒（与账本 wire 的精度口径一致）。 */
function sec(v: number | null | undefined): string {
  return typeof v === 'number' ? `${v.toFixed(2)}s` : '—'
}

/** 下行字节：push 走 worker 侧 result_bytes（hub 侧 down 口径为 pull 专用）。 */
function downBytesOf(w: IterWire): number | null {
  if (w.downBytes !== null) return w.downBytes
  const rb = w.worker?.result_bytes
  return typeof rb === 'number' ? rb : null
}

/** 有效上行吞吐（Mbps）：只有 up 字节与秒都有才算。 */
function upMbps(w: IterWire): string {
  if (w.upBytes === null || w.upSec === null || w.upSec <= 0) return '—'
  return `${((w.upBytes * 8) / w.upSec / 1e6).toFixed(1)} Mbps`
}

const TH = { textAlign: 'left' as const, padding: '2px 6px', whiteSpace: 'nowrap' as const }
const TD = { padding: '2px 6px', whiteSpace: 'nowrap' as const }

function WireNow({ row }: { row: IterRow }) {
  const w = row.wire ?? null
  if (!w) return null
  const items: Array<[string, string]> = [
    // 上行 = 本轮真正发出去的那一份（push = /job 体，含 manifest；pull = 服务出的 payload）。
    ['上行', `${fmtBytes(w.upBytes)} / ${sec(w.upSec)}（${upMbps(w)}）`],
    ['下行', fmtBytes(downBytesOf(w))],
    ['打包', sec(w.packSec)],
    ['blob 未命中', w.blobsMiss === null ? '—' : String(w.blobsMiss)],
  ]
  const worker = w.worker
  return (
    <div style={{ marginBottom: 8 }}>
      <div className="tc-small">
        <strong>本轮 it{row.iter}</strong> <span className="tc-muted">{row.time}</span>
      </div>
      <table style={{ marginTop: 4 }}>
        <tbody>
          {items.map(([k, v]) => (
            <tr key={k}>
              <th style={TH} className="tc-muted tc-small">
                {k}
              </th>
              <td style={TD} className="tc-small">
                {v}
              </td>
            </tr>
          ))}
          {/* worker 侧拆分（键集不固定，逐项列出——M0/M2 先后加过键，不硬编码白名单）。 */}
          {worker
            ? Object.entries(worker).map(([k, v]) => (
                <tr key={`w.${k}`}>
                  <th style={TH} className="tc-muted tc-small">
                    worker.{k}
                  </th>
                  <td style={TD} className="tc-small">
                    {v === null ? '—' : k.endsWith('_bytes') ? fmtBytes(v) : v.toFixed(2)}
                  </td>
                </tr>
              ))
            : null}
        </tbody>
      </table>
      <div className="tc-small tc-muted" style={{ marginTop: 4 }}>
        协议 {w.protocol ?? '—'} · 边缘 IP {w.edgeIp ?? '—'} · 瘦身{' '}
        {w.slim === null ? '—' : w.slim ? '开' : '关'} · rollout 源 {w.rolloutSrc ?? '—'}
      </div>
    </div>
  )
}

/** 最近若干轮走势：单点数字看不出「持续慢 vs 某轮退化」，故列成表。 */
function WireTrend({ rows }: { rows: IterRow[] }) {
  const withWire = rows.filter((r) => (r.wire ?? null) !== null).slice(0, 12)
  if (withWire.length === 0) return null
  return (
    <div style={{ marginBottom: 8 }}>
      <div className="tc-small" style={{ marginBottom: 4 }}>
        <strong>最近 {withWire.length} 轮</strong>
      </div>
      <table style={{ width: '100%' }}>
        <thead>
          <tr>
            <th style={TH} className="tc-small tc-muted">
              it
            </th>
            <th style={TH} className="tc-small tc-muted">
              上行
            </th>
            <th style={TH} className="tc-small tc-muted">
              上行秒
            </th>
            <th style={TH} className="tc-small tc-muted">
              下行
            </th>
            <th style={TH} className="tc-small tc-muted" title="打包 tar.xz（关键路径上）">
              打包
            </th>
            <th style={TH} className="tc-small tc-muted">
              blob
            </th>
            <th style={TH} className="tc-small tc-muted">
              协议 / 瘦身
            </th>
          </tr>
        </thead>
        <tbody>
          {withWire.map((r) => {
            const w = r.wire as IterWire
            return (
              <tr key={r.iter}>
                <td style={TD} className="tc-small">
                  {r.iter}
                </td>
                <td style={TD} className="tc-small">
                  {fmtBytes(w.upBytes)}
                </td>
                <td style={TD} className="tc-small">
                  {sec(w.upSec)}
                </td>
                <td style={TD} className="tc-small">
                  {fmtBytes(downBytesOf(w))}
                </td>
                <td style={TD} className="tc-small">
                  {sec(w.packSec)}
                </td>
                <td style={TD} className="tc-small">
                  {w.blobsMiss === null ? '—' : String(w.blobsMiss)}
                </td>
                <td style={TD} className="tc-small">
                  {w.protocol ?? '—'} / {w.slim === null ? '—' : w.slim ? '开' : '关'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 一条腿的标签：`http2` 常用腿正常显示，其余原样（探针支持任意 --legs）。 */
function legLabel(leg: string): string {
  return leg
}

function TunnelAbRunTable({ run }: { run: TunnelAbRun }) {
  const mb = run.bytes > 0 ? `${(run.bytes / 1024 / 1024).toFixed(0)}MiB` : '?'
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="tc-small">
        <strong>{run.file}</strong>{' '}
        <span className="tc-muted" title={fmtTs(run.mtime)}>
          {fmtRel(run.mtime)}
        </span>{' '}
        <span className="tc-muted tc-small">
          {mb} × {run.rounds} 发
        </span>
      </div>
      <table style={{ marginTop: 4 }}>
        <thead>
          <tr>
            <th style={TH} className="tc-small tc-muted">
              腿
            </th>
            <th style={TH} className="tc-small tc-muted" title="push 模式的真实大头是上行">
              方向
            </th>
            <th style={TH} className="tc-small tc-muted">
              p50
            </th>
            <th style={TH} className="tc-small tc-muted">
              p90
            </th>
            <th style={TH} className="tc-small tc-muted">
              max
            </th>
            <th style={TH} className="tc-small tc-muted">
              吞吐
            </th>
          </tr>
        </thead>
        <tbody>
          {run.rows.map((r) => (
            <tr key={`${r.leg}.${r.dir}`}>
              <td style={TD} className="tc-small">
                {legLabel(r.leg)}
              </td>
              <td style={TD} className="tc-small">
                {r.dir === 'up' ? '上行' : '下行'}
              </td>
              <td style={TD} className="tc-small">
                {sec(r.stat.p50Sec)}
              </td>
              <td style={TD} className="tc-small">
                {sec(r.stat.p90Sec)}
              </td>
              <td style={TD} className="tc-small">
                {sec(r.stat.maxSec)}
              </td>
              <td style={TD} className="tc-small">
                {r.stat.p50Mbps.toFixed(1)} Mbps
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {run.speedup.up !== null || run.speedup.down !== null ? (
        <div className="tc-small" style={{ marginTop: 4 }}>
          {run.speedup.up !== null ? (
            <span className="tc-badge tc-badge--g">
              http2 上行 p50 快 {run.speedup.up.toFixed(1)}×
            </span>
          ) : null}{' '}
          {run.speedup.down !== null ? (
            <span className="tc-badge tc-badge--g">下行快 {run.speedup.down.toFixed(1)}×</span>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function WirePanel({ stateView, course }: WirePanelProps) {
  const rows = stateView?.metrics.iters ?? []
  const latest = rows.find((r) => (r.wire ?? null) !== null) ?? null
  const ab = stateView?.tunnelAb ?? null
  const runs = ab?.runs ?? []

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        <span className="tc-badge tc-badge--a">传输账 wire</span>
        {latest?.wire?.protocol ? (
          <span className="tc-badge tc-badge--gray">隧道 {latest.wire.protocol}</span>
        ) : null}
        {latest?.wire?.slim != null ? (
          <span className={`tc-badge tc-badge--${latest.wire.slim ? 'g' : 'gray'}`}>
            瘦身 {latest.wire.slim ? '开' : '关'}
          </span>
        ) : null}
        <span className="tc-badge tc-badge--gray">A/B {runs.length} 次</span>
      </div>

      {latest === null ? (
        <div className="tc-caption" style={{ marginBottom: 10 }}>
          尚无传输账：账本里还没有带 <code>wire</code> 子字典的 iteration 行（该字段 2026-09-17 随
          M0 上线）；旧行无此键是预期空态，不是故障。
        </div>
      ) : (
        <WireNow row={latest} />
      )}

      <WireTrend rows={rows} />

      <div style={{ borderTop: '1px solid var(--line, #ddd)', margin: '10px 0' }} />

      <div className="tc-small" style={{ marginBottom: 4 }}>
        <strong>隧道 A/B 探针</strong>{' '}
        <span className="tc-muted tc-small">
          {course ? `本机闭环 · 与课程无关` : ''}（`tmp/tunnel-ab-*.json`，新→旧）
        </span>
      </div>
      {runs.length === 0 ? (
        <div className="tc-caption">
          还没有探针结果。跑一次：
          <br />
          <code>
            bun dashboard/src/launch/cli.ts --script remote/tunnel_ab_probe.py --legs quic,http2
            --rounds 5 --json-out tmp/tunnel-ab-1.json
          </code>
          <br />
          判据（计划 §3.4）：每臂 ≥2 个独立 run，`http2` 腿 p50 明显更快**且**连跑不退化。
        </div>
      ) : (
        runs.map((r) => <TunnelAbRunTable key={r.file} run={r} />)
      )}
      {ab?.error ? <div className="tc-caption">读探针结果出错：{ab.error}</div> : null}
    </div>
  )
}

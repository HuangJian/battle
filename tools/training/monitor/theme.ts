/** theme.ts — 监控页共享 CSS（从 pool-page.ts 提取的样式层）。 */

export function pageCss(): string {
  return `
:root{--bg:#f4f6f9;--card:#ffffff;--border:#e5e8ee;--text:#1c2333;--muted:#7a8395;
--green:#16a34a;--green-bg:#e9f9ef;--yellow:#b45309;--yellow-bg:#fdf3e7;--red:#dc2626;--red-bg:#fdecec;
--gray:#7a8395;--gray-bg:#f1f3f7;--accent:#2f5fe0;--accent-bg:#eef2fe;--row-hover:#f7f9fc}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
margin:0;background:var(--bg);color:var(--text);padding:24px 28px}
.wrap{max-width:1180px;margin:0 auto}
.pool-header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;
margin-bottom:16px}
.pool-header h2{margin:0;font-size:20px;font-weight:700;letter-spacing:.2px}
.pool-header h2 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green);
margin-right:8px;vertical-align:1px}
.pool-header .ts{font-size:12.5px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;
box-shadow:0 1px 3px rgba(16,24,40,.05)}
table{width:100%;border-collapse:collapse}
thead th{background:#fafbfd;border-bottom:2px solid var(--border);text-align:left;padding:11px 14px;
font-size:12px;font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none;
letter-spacing:.3px}
thead th:hover{background:#f0f3f8;color:var(--accent)}
tbody td{padding:10px 14px;border-bottom:1px solid #f0f2f6;font-size:13px;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--row-hover)}
tr.grp td{background:#eef2fb;color:var(--accent);font-weight:600;cursor:pointer;font-size:12.5px;
letter-spacing:.3px;user-select:none;border-bottom:1px solid #e2e8f4}
tr.grp:hover td{background:#e4ecfa}
tr.grp .caret{display:inline-block;width:14px}
td.name{font-weight:600}
td.name .dim{color:var(--muted);font-weight:400;font-size:12px}
td.ver{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#4b5563}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.evalrow td{background:#fafbfd;font-size:12px;padding:5px 14px;border-bottom:1px solid #f0f2f6}
#iters .evit{display:none}
#iters.f-eval .evit{display:inline}
#iters thead th{position:sticky;top:0;z-index:1}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;font-weight:600;line-height:18px;white-space:nowrap}
.b-green{background:var(--green-bg);color:var(--green)}
.b-yellow{background:var(--yellow-bg);color:var(--yellow)}
.b-red{background:var(--red-bg);color:var(--red)}
.b-gray{background:var(--gray-bg);color:var(--gray)}
.b-accent{background:var(--accent-bg);color:var(--accent)}
.pill{display:inline-block;margin-left:6px;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600;
background:var(--yellow-bg);color:var(--yellow);vertical-align:1px}
.err{color:var(--red);font-size:12px;word-break:break-all}
.muted{color:var(--muted);font-size:12px}
.foot{margin:16px 2px 0;font-size:12.5px;color:var(--muted);line-height:1.8}
.foot b{color:#59606f}
@media (max-width:900px){body{padding:14px}thead th,tbody td{padding:8px 10px}}
`
}

/** 共享客户端脚本：表头点击排序。 */
export function sortScript(): string {
  return `
function sortTbl(col, th) {
  const tb = document.querySelector('#pool tbody');
  const rows = Array.from(tb.rows);
  const dir = th.dataset.dir === 'asc' ? -1 : 1;
  th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  rows.sort((a, b) => {
    const av = a.cells[col].dataset.v ?? a.cells[col].innerText;
    const bv = b.cells[col].dataset.v ?? b.cells[col].innerText;
    const an = parseFloat(av), bn = parseFloat(bv);
    const cmp = !isNaN(an) && !isNaN(bn) ? an - bn : String(av).localeCompare(String(bv));
    return dir * cmp;
  });
  for (const r of rows) tb.appendChild(r);
}
`
}

/** 时间格式化（页面与服务端共用格式约定：本地时区 YYYY-MM-DD HH:MM:SS）。 */
export function fmtTs(ms: number): string {
  const d = new Date(ms)
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

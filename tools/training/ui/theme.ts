/** theme.ts — 训练控制台唯一 CSS 源（§5 token 体系 + tc- 前缀 BEM）。
 *
 *  吸收了 monitor/theme.ts 的全部样式并扩展为 token + 组件类。所有类带 tc- 前缀
 *  防作用域泄漏；spacing 4/8/12/16/24（8pt 栅格）、radius 8/10/14、shadow 2 级、
 *  字号 11.5/12/12.5/13/15/20。新增面板零 CSS —— 直接复用这里的基元类。
 */

export function pageCss(): string {
  return `
/* ── tokens ─────────────────────────────────────────────── */
:root{
  --bg:#f4f6f9;--card:#ffffff;--border:#e5e8ee;--text:#1c2333;--muted:#7a8395;
  --green:#16a34a;--green-bg:#e9f9ef;--yellow:#b45309;--yellow-bg:#fdf3e7;--red:#dc2626;--red-bg:#fdecec;
  --gray:#7a8395;--gray-bg:#f1f3f7;--accent:#2f5fe0;--accent-bg:#eef2fe;--row-hover:#f7f9fc;
  --mono:#4b5563;--topbar-h:#56px;
  --sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px;
  --r-1:8px;--r-2:10px;--r-3:14px;
  --sh-1:0 1px 3px rgba(16,24,40,.05);--sh-2:0 4px 16px rgba(16,24,40,.14);
  --fs-1:11.5px;--fs-2:12px;--fs-25:12.5px;--fs-3:13px;--fs-4:15px;--fs-5:20px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
  background:var(--bg);color:var(--text);font-size:var(--fs-3);line-height:1.5}
.tc-wrap{max-width:1180px;margin:0 auto;padding:var(--sp-6) var(--sp-6) var(--sp-6) var(--sp-6)}

/* ── 顶栏（吸顶 + 永远可见，DS-U8） ─────────────────────── */
.tc-topbar{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:var(--sp-4);
  flex-wrap:wrap;background:var(--bg);padding:var(--sp-3) 0;border-bottom:1px solid var(--border);margin-bottom:var(--sp-4)}
.tc-topbar h1{margin:0;font-size:var(--fs-5);font-weight:700;letter-spacing:.2px;display:flex;align-items:center;gap:var(--sp-2)}
.tc-topbar h1 :global(.dot){display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green)}
.tc-topbar__right{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;font-size:var(--fs-25)}
.tc-topbar__ts{font-size:var(--fs-25);color:var(--muted);white-space:nowrap}
.tc-topbar__anchors{display:flex;gap:var(--sp-2);flex-wrap:wrap;align-items:center;font-size:var(--fs-2)}
.tc-anchor{color:var(--muted);text-decoration:none;cursor:pointer;padding:2px 8px;border-radius:999px}
.tc-anchor:hover{color:var(--accent);background:var(--accent-bg)}
.tc-stopall{border:1.5px solid var(--red);color:var(--red);background:var(--card);border-radius:var(--r-1);
  padding:6px 12px;font-weight:600;cursor:pointer;transition:all .15s}
.tc-stopall--arm{border-color:var(--red);background:var(--red);color:#fff}
.tc-stopall:disabled{opacity:.5;cursor:not-allowed}
select.tc-sel{padding:5px 9px;border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:var(--fs-25);cursor:pointer}
button.tc-btn{padding:5px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);cursor:pointer;font-size:var(--fs-3)}
button.tc-btn:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button.tc-btn:disabled{opacity:.45;cursor:not-allowed}
button.tc-btn--danger{border-color:var(--red);color:var(--red)}
button.tc-btn--sm{padding:3px 9px;font-size:var(--fs-2)}
a.tc-btn{display:inline-block;text-decoration:none;text-align:center}

/* ── 全局提示条（dirty / 连接中断） ─────────────────────── */
.tc-banner{display:flex;align-items:center;gap:var(--sp-3);padding:8px 14px;border-radius:var(--r-2);
  font-size:var(--fs-2);margin-bottom:var(--sp-3);flex-wrap:wrap}
.tc-banner--dirty{background:var(--yellow-bg);color:var(--yellow);border:1px solid var(--yellow)}
.tc-banner--err{background:var(--red-bg);color:var(--red);border:1px solid var(--red)}
.tc-banner button{margin-left:auto}

/* ── Card（折叠 / 最大化 / 陈旧度 / 卡级动作） ───────────── */
.tc-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);overflow:hidden;
  box-shadow:var(--sh-1);margin-bottom:var(--sp-4)}
.tc-card--max{position:fixed;inset:56px 12px 12px;z-index:50;margin:0;
  display:flex;flex-direction:column;box-shadow:var(--sh-2)}
.tc-card__hd{display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--border)}
.tc-card__title{font-size:var(--fs-4);font-weight:600;margin:0}
.tc-card__sub{color:var(--muted);font-size:var(--fs-2);font-weight:400}
.tc-card__actions{margin-left:auto;display:flex;align-items:center;gap:var(--sp-2)}
.tc-iconbtn{border:1px solid transparent;background:none;cursor:pointer;padding:3px 7px;border-radius:var(--r-1);
  color:var(--muted);font-size:var(--fs-3);line-height:1}
.tc-iconbtn:hover{background:var(--row-hover);color:var(--text)}
.tc-card__body{padding:var(--sp-4)}
.tc-card__foot{padding:var(--sp-2) var(--sp-4);border-top:1px solid #f0f2f6;font-size:var(--fs-2);color:var(--muted)}
.tc-card__err{border-top:2px solid var(--red);background:var(--red-bg);color:var(--red);padding:6px 14px;
  font-size:var(--fs-2);display:flex;gap:var(--sp-3);align-items:center}
.tc-card__err button{margin-left:auto}
.tc-collapse{display:grid;grid-template-rows:1fr;transition:grid-template-rows 120ms ease-out}
.tc-collapse--on{grid-template-rows:0fr}
.tc-collapse > div{overflow:hidden;min-height:0}

/* 陈旧度圆点：颜色+形状双编码（色盲可读，GLM-U7） */
.tc-stale{width:10px;height:10px;display:inline-block;flex:none}
.tc-stale--ok{background:var(--green);border-radius:50%}
.tc-stale--refresh{background:var(--yellow);clip-path:polygon(50% 0,50% 100%,100% 100%,100% 0)}
.tc-stale--err{background:var(--red);border-radius:0}
.tc-stale-wrap{display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-2);color:var(--muted);cursor:default}

/* ── Pill / Badge ───────────────────────────────────────── */
.tc-pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:var(--fs-1);font-weight:600;white-space:nowrap}
.tc-pill--g{background:var(--green-bg);color:var(--green)}
.tc-pill--y{background:var(--yellow-bg);color:var(--yellow)}
.tc-pill--r{background:var(--red-bg);color:var(--red)}
.tc-pill--gray{background:var(--gray-bg);color:var(--gray)}
.tc-pill--a{background:var(--accent-bg);color:var(--accent)}
.tc-badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:var(--fs-2);font-weight:600;line-height:18px;white-space:nowrap}
.tc-badge--g{background:var(--green-bg);color:var(--green)}
.tc-badge--y{background:var(--yellow-bg);color:var(--yellow)}
.tc-badge--r{background:var(--red-bg);color:var(--red)}
.tc-badge--gray{background:var(--gray-bg);color:var(--gray)}
.tc-badge--a{background:var(--accent-bg);color:var(--accent)}
.tc-pill--note{background:var(--yellow-bg);color:var(--yellow);margin-left:6px}

/* ── 表格（粘性表头/首列 + 密度 + 显隐 + 行展开） ────────── */
.tc-tablewrap{overflow:auto;max-height:min(60vh,720px)}
.tc-card--max .tc-tablewrap{flex:1;max-height:none;min-height:0}
.tc-table{width:100%;border-collapse:separate;border-spacing:0}
.tc-table thead th{background:#fafbfd;border-bottom:2px solid var(--border);text-align:left;padding:11px 14px;
  font-size:var(--fs-2);font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none;
  letter-spacing:.3px;position:sticky;top:0;z-index:2}
.tc-table thead th:hover{background:#f0f3f8;color:var(--accent)}
.tc-table tbody td{padding:10px 14px;border-bottom:1px solid #f0f2f6;font-size:var(--fs-3);vertical-align:middle}
.tc-table tbody tr:last-child td{border-bottom:none}
.tc-table tbody tr:hover td{background:var(--row-hover)}
.tc-table th.tc-sort-desc::after{content:" ▾";color:var(--accent)}
.tc-table th.tc-sort-asc::after{content:" ▴";color:var(--accent)}
.tc-table th.thhl{background:#eef2fe;box-shadow:inset 3px 0 0 var(--accent)}
.tc-table td.tdhl{box-shadow:inset 3px 0 0 var(--accent)}
.tc-table--dense tbody td{padding:5px 10px;font-size:var(--fs-25)}
.tc-table--dense thead th{padding:7px 10px}
.tc-num{text-align:right;font-variant-numeric:tabular-nums}
.tc-firstcol{position:sticky;left:0;background:#fff;z-index:1}
.tc-table thead th.tc-firstcol{background:#fafbfd;z-index:3}
.tc-table tbody tr:hover td.tc-firstcol{background:#f1f4fa}
.tc-row-expand td{background:#fafbfd;font-size:var(--fs-2);padding:8px 14px;border-bottom:1px solid #f0f2f6}
.tc-row-expand .tc-recent-dots{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.tc-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.tc-dot--ok{background:var(--green)}
.tc-dot--fail{background:var(--red)}
.tc-dot--empty{background:var(--border)}
.tc-empty,.tc-loading{padding:20px;text-align:center;color:var(--muted);font-size:var(--fs-2)}
.tc-errtext{color:var(--red);font-size:var(--fs-2);word-break:break-all}
.tc-caption{padding:var(--sp-2) var(--sp-4);border-top:1px solid #f0f2f6;font-size:var(--fs-2);color:var(--muted);line-height:1.8}
.tc-caption b{color:#59606f}

/* ── 表格工具栏（过滤 / 列 / 密度 / 关键词） ─────────────── */
.tc-toolbar{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;padding:var(--sp-3) var(--sp-4) 0;font-size:var(--fs-2);color:var(--muted)}
.tc-toolbar input[type=text]{padding:4px 8px;border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:var(--fs-2)}
.tc-colmenu{position:relative}
.tc-colmenu__panel{position:absolute;right:0;top:24px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--r-2);box-shadow:var(--sh-2);padding:var(--sp-2);z-index:20;min-width:150px}
.tc-colmenu__panel label{display:flex;align-items:center;gap:6px;padding:3px 6px;cursor:pointer;font-size:var(--fs-2)}

/* ── Segmented（节点卡 控制|统计 / 预设按钮） ────────────── */
.tc-segmented{display:inline-flex;gap:4px;padding:3px;background:var(--gray-bg);border-radius:var(--r-1)}
.tc-segmented__btn{border:none;background:none;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:var(--fs-2);color:var(--muted)}
.tc-segmented__btn--on{background:var(--card);color:var(--accent);font-weight:700;box-shadow:var(--sh-1)}
.tc-preset{padding:7px 14px;border:1.5px solid var(--border);border-radius:9px;background:var(--card);cursor:pointer;font-size:var(--fs-3)}
.tc-preset--on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);font-weight:700}
a.tc-preset{text-decoration:none;display:inline-block}

/* ── Toggle（原生 checkbox，语义自带） ───────────────────── */
.tc-toggle{display:inline-flex;align-items:center;gap:7px;margin:4px 10px 4px 0;cursor:pointer;font-size:var(--fs-3)}
.tc-toggle code{font-size:var(--fs-2);color:var(--muted)}
.tc-toggle input[type=checkbox]{accent-color:var(--accent)}
.tc-conc{width:64px;padding:4px 7px;border:1px solid var(--border);border-radius:7px;font-size:var(--fs-3)}

/* ── Flash（动作结果/全局） ──────────────────────────────── */
.tc-flash{position:fixed;top:14px;right:14px;max-width:460px;padding:10px 14px;border-radius:var(--r-2);
  font-size:var(--fs-3);white-space:pre-wrap;z-index:80;box-shadow:var(--sh-2);display:none}
.tc-flash--show{display:block}
.tc-flash--ok{background:var(--green-bg);color:var(--green);border:1px solid var(--green)}
.tc-flash--bad{background:var(--red-bg);color:var(--red);border:1px solid var(--red)}

/* ── Sparkline 概览条 ────────────────────────────────────── */
.tc-spark-strip{display:flex;gap:var(--sp-4);flex-wrap:wrap;margin:var(--sp-1) 0 var(--sp-4)}
.tc-spark-cell{flex:1;min-width:130px;background:#fafbfd;border:1px solid var(--border);border-radius:var(--r-2);padding:7px 10px;cursor:pointer}
.tc-spark-cell--on{outline:2px solid var(--accent)}
.tc-spark-head{display:flex;justify-content:space-between;align-items:baseline;font-size:var(--fs-2);color:var(--muted);margin-bottom:2px}
.tc-spark-head b{color:var(--text);font-size:var(--fs-3)}
.spark{display:block}
.tc-hl{animation:tc-hl 300ms ease-out}
@keyframes tc-hl{0%{background:#fff3bf}100%{background:transparent}}

/* ── 组件卡细节 ──────────────────────────────────────────── */
.tc-mono{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace}
.tc-muted{color:var(--muted)}
.tc-small{font-size:var(--fs-2)}
.tc-row{display:flex;gap:var(--sp-6);flex-wrap:wrap}
.tc-col{flex:1;min-width:320px}
.tc-logtail{max-height:150px;overflow:auto;font-size:var(--fs-1);background:#f7f8fa;padding:6px 9px;border-radius:8px;margin-top:5px;white-space:pre-wrap;word-break:break-all}
.tc-subhead{margin:4px 0 2px;font-size:var(--fs-3);color:var(--muted)}

/* ── 日志页 ──────────────────────────────────────────────── */
.tc-logbox{background:#10141f;color:#d6e2f0;padding:14px 16px;border-radius:var(--r-3);overflow:auto;
  max-height:calc(100vh - 260px);font-size:var(--fs-2);line-height:1.55;
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;white-space:pre-wrap;word-break:break-all}
.tc-log-toggle{position:fixed;right:24px;bottom:24px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--r-2);box-shadow:var(--sh-2);padding:8px 14px;font-size:var(--fs-2);cursor:pointer;z-index:30}
.tc-log-toggle:hover{border-color:var(--accent);color:var(--accent)}

/* ── 响应式 ──────────────────────────────────────────────── */
@media (max-width:900px){.tc-wrap{padding:var(--sp-4)}.tc-table thead th,.tc-table tbody td{padding:8px 10px}.tc-topbar{gap:var(--sp-2)}}
`
}

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
  --eval-line:#ea580c;
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
.tc-wrap{max-width:1180px;margin:0 auto;padding:var(--sp-3) var(--sp-4) var(--sp-4)} /* 纵向密度：上 12 / 左右 16 / 下 16 */

/* ── 顶栏（吸顶 + 永远可见，DS-U8）+ 训练状态条 ─────────── */
.tc-topbar{position:sticky;top:0;z-index:60;display:flex;flex-direction:column;gap:4px;
  background:var(--bg);padding:6px 0;border-bottom:1px solid var(--border);margin-bottom:10px}
.tc-topbar__row{display:flex;align-items:center;gap:var(--sp-4);flex-wrap:wrap}
/* 课程选择：标题行中部（flex:1 吃掉左右剩余，内部居中 —— §382 标题行置顶 + 指标 chips 移除） */
.tc-topbar__course{flex:1;display:flex;align-items:center;justify-content:center;gap:var(--sp-2);font-size:var(--fs-2);color:var(--muted);white-space:nowrap}
.tc-topbar__course .tc-sel{max-width:220px}
.tc-h1{margin:0;font-size:var(--fs-5);font-weight:700;letter-spacing:.2px;display:flex;align-items:center;gap:var(--sp-2)}
.tc-badge--status{display:inline-flex;align-items:center;gap:6px;background:var(--accent-bg);color:var(--accent);
  border-radius:999px;padding:2px 10px;font-size:var(--fs-2);font-weight:600;white-space:nowrap}
.tc-topbar h1{margin:0;font-size:var(--fs-5);font-weight:700;letter-spacing:.2px;display:flex;align-items:center;gap:var(--sp-2)}
.tc-topbar h1 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green)}
.tc-topbar__right{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;font-size:var(--fs-25);margin-left:auto} /* 右对齐到页面右侧（§367 UI） */
.tc-topbar__ts{font-size:var(--fs-25);color:var(--muted);white-space:nowrap}
.tc-topbar__anchors{display:flex;gap:var(--sp-2);flex-wrap:wrap;align-items:center;font-size:var(--fs-2)}
.tc-anchor{color:var(--muted);text-decoration:none;cursor:pointer;padding:2px 8px;border-radius:999px}
.tc-anchor:hover{color:var(--accent);background:var(--accent-bg)}
.tc-stopall{border:1.5px solid var(--red);color:var(--red);background:var(--card);border-radius:var(--r-1);
  padding:6px 12px;font-weight:600;cursor:pointer;transition:all .15s}
.tc-stopall--arm{border-color:var(--red);background:var(--red);color:#fff}
.tc-stopall:disabled{opacity:.5;cursor:not-allowed}
select.tc-sel{padding:3px 8px;border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:var(--fs-25);cursor:pointer}
select.tc-sel:disabled{opacity:.55;cursor:not-allowed;background:var(--gray-bg)}
button.tc-btn{padding:4px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);cursor:pointer;font-size:var(--fs-3)}
button.tc-btn:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button.tc-btn:disabled{opacity:.45;cursor:not-allowed}
button.tc-btn--primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button.tc-btn--primary:hover:not(:disabled){background:#224bc8;border-color:#224bc8;color:#fff}
button.tc-btn--danger{border-color:var(--red);color:var(--red)}
button.tc-btn--sm{padding:2px 8px;font-size:var(--fs-2)}
a.tc-btn{display:inline-block;text-decoration:none;text-align:center}

/* ── 全局提示条（dirty / 连接中断） ─────────────────────── */
.tc-banner{display:flex;align-items:center;gap:var(--sp-3);padding:6px 12px;border-radius:var(--r-2);
  font-size:var(--fs-2);margin-bottom:8px;flex-wrap:wrap}
.tc-banner--dirty{background:var(--yellow-bg);color:var(--yellow);border:1px solid var(--yellow)}
.tc-banner--err{background:var(--red-bg);color:var(--red);border:1px solid var(--red)}
.tc-banner--info{background:var(--card);color:var(--muted);border:1px solid var(--border)}
/* 局域网只读横幅（§2026-09-09-goalnn-console-lan-readonly 延伸）：琥珀色 + 加粗，一眼可见 */
.tc-banner--ro{background:var(--yellow-bg);color:#9a3f00;border:1.5px solid var(--yellow);font-weight:600}
/* 云端停机：已恢复（§386 灰横幅：曾停机但已恢复，历史提示非告警） */
.tc-banner--muted{background:var(--gray-bg);color:#5b6472;border:1px solid #c9ced8}
.tc-banner button{margin-left:auto}

/* ── Card（折叠 / 最大化 / 陈旧度 / 卡级动作） ───────────── */
.tc-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);overflow:hidden;
  box-shadow:var(--sh-1);margin-bottom:var(--sp-4)}
.tc-card--max{position:fixed;inset:56px 12px 12px;z-index:50;margin:0;
  display:flex;flex-direction:column;box-shadow:var(--sh-2)}
.tc-card__hd{display:flex;align-items:center;gap:var(--sp-3);padding:8px 12px;border-bottom:1px solid var(--border)}
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
/* 局域网只读角标：琥珀描边 + 外发光，吸顶顶栏常驻可见 */
.tc-badge--ro{background:var(--yellow-bg);color:#9a3f00;border:1px solid var(--yellow);box-shadow:0 0 0 3px var(--yellow-bg);font-weight:700}
.tc-pill--note{background:var(--yellow-bg);color:var(--yellow);margin-left:6px}

/* ── 表格（粘性表头/首列 + 密度 + 显隐 + 行展开） ────────── */
.tc-tablewrap{overflow:auto;max-height:min(60vh,720px)}
.tc-card--max .tc-tablewrap{flex:1;max-height:none;min-height:0}
/* 数据表弹性列：抽屉（或等高 card）内根容器竖直撑满，滚动收在 tablewrap */
.tc-dtable{display:flex;flex-direction:column;min-height:0}
.tc-table{width:100%;border-collapse:separate;border-spacing:0}
.tc-table thead th{background:#fafbfd;border-bottom:2px solid var(--border);text-align:left;padding:11px 14px;
  font-size:var(--fs-2);font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none;
  letter-spacing:.3px;position:sticky;top:0;z-index:2}
.tc-table thead th:hover{background:#f0f3f8;color:var(--accent)}
.tc-table tbody td{padding:10px 14px;border-bottom:1px solid #f0f2f6;font-size:var(--fs-3);vertical-align:middle;background:#fff}
.tc-table tbody tr:last-child td{border-bottom:none}
/* 斑马纹 + hover 三色区分：奇行白 / 偶行 #f4f6fa / hover #e9edf7（hover 放其后、同特异性时胜出） */
.tc-table tbody tr:nth-child(even) td{background:#f4f6fa}
.tc-table tbody tr:hover td{background:#e9edf7}
.tc-table th.tc-sort-desc::after{content:" ▾";color:var(--accent)}
.tc-table th.tc-sort-asc::after{content:" ▴";color:var(--accent)}
.tc-table th.thhl{background:#eef2fe;box-shadow:inset 3px 0 0 var(--accent)}
.tc-table td.tdhl{box-shadow:inset 3px 0 0 var(--accent)}
.tc-table--dense tbody td{padding:5px 10px;font-size:var(--fs-25)}
.tc-table--dense thead th{padding:7px 10px}
.tc-num{text-align:right;font-variant-numeric:tabular-nums}
.tc-firstcol{position:sticky;left:0;background:#fff;z-index:1}
.tc-table thead th.tc-firstcol{background:#fafbfd;z-index:3}
.tc-table tbody tr:hover td.tc-firstcol{background:#e9edf7}
.tc-row-expand td{background:#fafbfd;font-size:var(--fs-2);padding:8px 14px;border-bottom:1px solid #f0f2f6}
.tc-row-expand .tc-recent-dots{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.tc-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.tc-dot--ok{background:var(--green)}
.tc-dot--fail{background:var(--red)}
.tc-dot--empty{background:var(--border)}
.tc-empty,.tc-loading{padding:20px;text-align:center;color:var(--muted);font-size:var(--fs-2)}
.tc-errtext{color:var(--red);font-size:var(--fs-2);word-break:break-all}
.tc-caption{padding:6px 16px;border-top:1px solid #f0f2f6;font-size:var(--fs-2);color:var(--muted);line-height:1.6}
.tc-caption b{color:#59606f}

/* ── 首页节点行下方 EvalBoard 摘要（列 = 阶梯 8 级） ──────── */
.tc-eval-summary{margin-top:8px;background:var(--card);border:1px solid var(--border);border-radius:var(--r-2);box-shadow:var(--sh-1)}
.tc-eval-summary__hd{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-3);flex-wrap:wrap;padding:8px 12px 0}
.tc-eval-summary__title{margin:0;font-size:var(--fs-4);font-weight:700;display:flex;align-items:baseline;gap:4px;flex-wrap:wrap}
.tc-eval-summary__wrap{max-height:none;overflow:auto}
.tc-eval-summary__note{padding:4px 12px 8px;margin:0}
.tc-eval-summary .tc-table tbody td{padding:4px 10px}
.tc-eval-summary__empty{padding:var(--sp-4)}
.tc-eval-summary__empty p{margin:4px 0}
.tc-metric-toggles{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;margin-right:var(--sp-2)}
.tc-metric-toggles label{display:inline-flex;align-items:center;gap:3px;white-space:nowrap}

/* ── 表格工具栏（过滤 / 列 / 密度 / 关键词 / 返回顶部） ──────── */
.tc-toolbar{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;padding:var(--sp-3) var(--sp-4) 0;font-size:var(--fs-2);color:var(--muted)}
.tc-toolbar input[type=text]{padding:4px 8px;border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:var(--fs-2)}
.tc-toolbar__totop{margin-left:auto} /* 「返回顶部」贴工具栏最右缘 */
.tc-colmenu{position:relative}
.tc-colmenu__panel{position:absolute;right:0;top:24px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--r-2);box-shadow:var(--sh-2);padding:var(--sp-2);z-index:20;min-width:150px}
.tc-colmenu__panel label{display:flex;align-items:center;gap:6px;padding:3px 6px;cursor:pointer;font-size:var(--fs-2)}

/* ── Segmented（统一胶囊分段控件：节点卡/密度/行过滤/trainer 模式） ── */
.tc-segmented{display:inline-flex;align-items:center}
.tc-segmented__btn{border:1px solid var(--border);background:var(--card);padding:3px 11px;font-size:var(--fs-2);color:var(--muted);cursor:pointer;transition:all .12s;white-space:nowrap}
.tc-segmented__btn:first-child{border-radius:999px 0 0 999px}
.tc-segmented__btn:last-child{border-radius:0 999px 999px 0}
.tc-segmented__btn:not(:first-child){border-left:none}
.tc-segmented__btn:hover{color:var(--accent)}
.tc-segmented__btn--on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.tc-segmented__btn--on:hover{color:#fff}
.tc-preset{padding:7px 14px;border:1.5px solid var(--border);border-radius:9px;background:var(--card);cursor:pointer;font-size:var(--fs-3)}
.tc-preset--on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);font-weight:700}
a.tc-preset{text-decoration:none;display:inline-block}

/* ── Toggle（原生 checkbox，语义自带） ───────────────────── */
.tc-toggle{display:inline-flex;align-items:center;gap:7px;margin:4px 10px 4px 0;cursor:pointer;font-size:var(--fs-3)}
.tc-toggle code{font-size:var(--fs-2);color:var(--muted)}
.tc-toggle input[type=checkbox]{accent-color:var(--accent)}
.tc-conc{width:64px;padding:4px 7px;border:1px solid var(--border);border-radius:7px;font-size:var(--fs-3)}

/* ── Modal 内 toggle 组（统一视觉，非散落独立按键） ────────── */
.tc-toggle-group{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}
.tc-toggle-group .tc-toggle{margin:0;padding:5px 12px;border:1px solid var(--border);border-radius:var(--r-2);background:var(--card);font-size:var(--fs-2);font-weight:600;transition:all .12s;min-width:72px;justify-content:center}
.tc-toggle-group .tc-toggle:hover{border-color:var(--accent);color:var(--accent)}
.tc-toggle-group .tc-toggle input[type=checkbox]{accent-color:var(--accent);margin-right:6px}
.tc-toggle-group .tc-toggle input[type=checkbox]:checked+span{color:var(--accent);font-weight:700}

/* ── Flash（动作结果/全局） ──────────────────────────────── */
.tc-flash{position:fixed;top:14px;right:14px;max-width:460px;padding:10px 14px;border-radius:var(--r-2);
  font-size:var(--fs-3);white-space:pre-wrap;z-index:80;box-shadow:var(--sh-2);display:none}
.tc-flash--show{display:block}
.tc-flash--ok{background:var(--green-bg);color:var(--green);border:1px solid var(--green)}
.tc-flash--bad{background:var(--red-bg);color:var(--red);border:1px solid var(--red)}

/* ── 走势图（轻量 SVG + 悬停坐标） ────────────────────── */
.tc-trend{position:relative;width:100%;min-height:40px}
.tc-trend__svg{display:block;width:100%;height:100%;overflow:visible}
.tc-trend__tip{position:absolute;top:0;transform:translate(-50%,-100%);display:flex;flex-direction:column;align-items:center;gap:1px;padding:3px 7px;border-radius:6px;background:var(--card);border:1px solid var(--border);box-shadow:var(--sh-1);pointer-events:none;white-space:nowrap;z-index:5;font-size:10.5px;line-height:1.3;margin-top:-4px}
.tc-trend__tip-it{color:var(--muted)}
.tc-trend__tip-val{color:var(--text);font-weight:700;font-variant-numeric:tabular-nums}

/* 走势范围档位（全量 / 最近30 / 最近10）：胶囊分段控件，非独立按键 */
.tc-trend-range{display:flex;gap:0;margin-bottom:4px;align-self:flex-start}
.tc-trend-range__btn{border:1px solid var(--border);background:var(--card);padding:3px 11px;font-size:var(--fs-2);color:var(--muted);cursor:pointer;transition:all .12s}
.tc-trend-range__btn:first-child{border-radius:999px 0 0 999px}
.tc-trend-range__btn:last-child{border-radius:0 999px 999px 0}
.tc-trend-range__btn:not(:first-child){border-left:none}
.tc-trend-range__btn:hover{color:var(--accent)}
.tc-trend-range__btn--on{background:var(--accent);border-color:var(--accent);color:#fff}
.tc-trend-range__btn--on:hover{color:#fff}
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
.tc-link{background:none;border:none;padding:0;color:var(--accent);cursor:pointer;font-size:var(--fs-2);font-weight:600}
.tc-link:hover{text-decoration:underline}

/* ── 日志页（§367 重设计：终端美学 + 结构化日志 + 搜索/过滤交互） ───────────────────────────── */
.tc-logwrap{padding:var(--sp-4) 0 var(--sp-6);display:flex;flex-direction:column;gap:var(--sp-3)}

/* 顶卡 */
.tc-loghead{display:flex;align-items:center;gap:var(--sp-3);background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);padding:10px var(--sp-4);flex-wrap:wrap}
.tc-loghead__dot{width:10px;height:10px;border-radius:50%;background:var(--gray);flex-shrink:0}
.tc-loghead__dot--live{background:var(--green);box-shadow:0 0 0 3px var(--green-bg);animation:tc-live 1.6s ease-in-out infinite}
@keyframes tc-live{0%,100%{opacity:1}50%{opacity:.45}}
.tc-loghead h1{font-size:var(--fs-4);margin:0;display:flex;align-items:baseline;gap:6px;white-space:nowrap}
.tc-loghead__sep{color:var(--muted);font-weight:400}
.tc-loghead__comp{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace}
.tc-loghead__meta{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;min-width:0}
.tc-logpath{font-size:var(--fs-2);color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:min(46vw,420px)}
.tc-loghead__back{margin-left:auto}
.tc-chip{display:inline-flex;align-items:center;gap:4px;padding:1px 8px;border-radius:999px;font-size:var(--fs-2);background:var(--accent-bg);color:var(--accent);white-space:nowrap}
.tc-chip b{font-weight:700}
.tc-chip--amber{background:#fdf3e7;color:#b45309}
.tc-chip--red{background:var(--red-bg);color:var(--red)}
.tc-chip--muted{background:var(--gray-bg);color:var(--muted)}

/* 吸顶工具栏 */
.tc-logtool{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;position:sticky;top:0;z-index:20;background:var(--bg);padding:8px 2px;border-bottom:1px solid var(--border)}
.tc-logtool__nav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.tc-lognav{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--border);border-radius:999px;font-size:var(--fs-2);color:var(--muted);text-decoration:none;white-space:nowrap}
.tc-lognav:hover{border-color:var(--accent);color:var(--accent)}
.tc-lognav--on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.tc-lognav--on .tc-dot--on{background:rgba(255,255,255,.85)}
.tc-logtool__right{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;margin-left:auto}
.tc-logtool__search{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:999px;padding:3px 10px;background:var(--card);min-width:180px}
.tc-logtool__search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg)}
.tc-logtool__search-icon{color:var(--muted);font-size:13px}
.tc-logtool__search input{border:none;outline:none;background:none;font-size:var(--fs-2);width:100%;color:var(--text)}
.tc-logtool__clear{border:none;background:none;color:var(--muted);cursor:pointer;font-size:11px;padding:0 2px}
.tc-logtool__clear:hover{color:var(--red)}
.tc-logtool__lines{display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-2);color:var(--muted)}

/* 暂停跟随提示条（§373：并入工具栏最左 inline pill，不再占独立纵向行） */
.tc-logpause{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:999px;background:var(--yellow-bg);color:#b45309;font-size:var(--fs-2);font-weight:600;white-space:nowrap}

/* 日志面板：终端风 */
.tc-logpanel{background:#0d1117;border:1px solid #232a36;border-radius:var(--r-3);overflow:hidden;box-shadow:var(--sh-1)}
.tc-logpanel__hd{display:flex;align-items:center;gap:var(--sp-2);padding:8px 12px;background:#161c26;border-bottom:1px solid #232a36;color:#8b98ab;font-size:var(--fs-2)}
.tc-logpanel__dots{display:inline-flex;gap:5px}
.tc-logpanel__dots i{width:10px;height:10px;border-radius:50%;background:#ff5f57}
.tc-logpanel__dots i:nth-child(2){background:#febc2e}
.tc-logpanel__dots i:nth-child(3){background:#28c840}
.tc-logpanel__file{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;margin-left:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:52vw}
.tc-logpanel__live{margin-left:auto;display:inline-flex;align-items:center;gap:5px;color:#28c840;font-weight:700;letter-spacing:.5px;font-size:10.5px}
.tc-logpanel__live i{width:7px;height:7px;border-radius:50%;background:#28c840;animation:tc-live 1.6s ease-in-out infinite}
.tc-logpanel__count{font-size:var(--fs-2);color:#8b98ab}

.tc-logbox{flex:1;overflow:auto;max-height:calc(100vh - 250px);padding:6px 0 14px;scroll-behavior:auto}
.tc-logline{display:grid;grid-template-columns:44px 78px auto 1fr;align-items:start;gap:8px;padding:1px 12px;font-size:var(--fs-2);line-height:1.6;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;white-space:pre-wrap;word-break:break-all;color:#c9d4e3;border-left:2px solid transparent}
.tc-logline:hover{background:rgba(255,255,255,.045)}
.tc-logline__no{color:#4b5563;text-align:right;user-select:none;font-variant-numeric:tabular-nums}
.tc-logline__ts{color:#5b6774;user-select:none;font-variant-numeric:tabular-nums}
.tc-logline__text{min-width:0}
.tc-logline mark{background:#b45309;color:#fff;padding:0 1px;border-radius:2px}
.tc-logline--warn{border-left-color:var(--yellow);background:rgba(180,83,9,.07)}
.tc-logline--warn:hover{background:rgba(180,83,9,.12)}
.tc-logline--error{border-left-color:var(--red);background:rgba(220,38,38,.08)}
.tc-logline--error:hover{background:rgba(220,38,38,.13)}
.tc-logline--error .tc-logline__text{color:#f3b4b4}
.tc-logline--ev{display:block;padding:3px 12px 3px 0;grid-template-columns:none;background:rgba(47,95,224,.05);border-left:2px solid var(--accent)}
.tc-logline--ev.tc-logline--ev-iter_error{border-left-color:var(--red);background:rgba(220,38,38,.08)}
.tc-logline--ev-iter_error .tc-logchip{background:var(--red);color:#fff}
.tc-logline--ev-run_start .tc-logchip,.tc-logline--ev-job_completed .tc-logchip{background:var(--green);color:#fff}
.tc-logline--ev-iteration .tc-logchip{background:var(--accent);color:#fff}
.tc-logline--ev-job_pending .tc-logchip{background:var(--yellow);color:#fff}
.tc-logline__body{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.tc-logchip{display:inline-block;padding:0 7px;border-radius:6px;font-size:10.5px;font-weight:700;background:#232a36;color:#aeb9c9;white-space:nowrap}
.tc-logchip--tag{background:transparent;color:#7d8ea3;font-weight:600;border:1px solid #2b3442}
.tc-logkv{display:inline-flex;align-items:baseline;gap:4px;white-space:nowrap}
.tc-logkv__k{color:#7d8ea3;font-size:10.5px}
.tc-logkv__v{color:#dbe4f0;font-weight:600}
.tc-logkv__err{color:#f3b4b4;font-weight:600;font-size:var(--fs-2);word-break:break-all}
.tc-logempty{display:flex;flex-direction:column;align-items:center;gap:6px;padding:48px 16px;color:#8b98ab;text-align:center}
.tc-logempty__icon{font-size:26px}

/* 直达底部（工具栏内联按钮；§372 不再浮动） */
.tc-logbtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:999px;background:var(--card);padding:4px 12px;font-size:var(--fs-2);font-weight:600;color:var(--text);cursor:pointer;white-space:nowrap}
.tc-logbtn:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.tc-logbtn--bottom{opacity:.55;cursor:default}
.tc-logbtn__icon{color:var(--accent);font-size:13px}
.tc-logbtn__badge{min-width:18px;text-align:center;padding:0 5px;border-radius:999px;background:var(--accent);color:#fff;font-size:10.5px;font-weight:700;animation:tc-live 1.2s ease-in-out infinite}

/* ── 响应式 ──────────────────────────────────────────────── */
@media (max-width:900px){.tc-wrap{padding:var(--sp-4)}.tc-table thead th,.tc-table tbody td{padding:8px 10px}.tc-topbar{gap:var(--sp-2)}}
/* ── 一屏仪表盘（用户在 plan §4 定稿的紧凑布局；DECISIONS §355） ── */

/* hero：训练状态焦点 */
.tc-hero{display:flex;gap:16px;align-items:stretch;background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);padding:10px 12px;margin-bottom:10px;flex-wrap:wrap}
.tc-hero__kpi{display:flex;flex-direction:column;gap:var(--sp-2);min-width:170px}
.tc-hero__lbl{font-size:var(--fs-2);color:var(--muted)}
.tc-hero__val{font-size:34px;font-weight:800;line-height:1.1;font-variant-numeric:tabular-nums;color:var(--text)}
.tc-hero__val--danger{color:var(--red)}
.tc-hero__val--ok{color:var(--green)}
.tc-hero__sub{font-size:var(--fs-2);color:var(--muted)}
.tc-hero__right{flex:1;display:flex;flex-direction:column;gap:var(--sp-2);min-width:300px}

/* hero 右侧趋势网格：三行八格（行1 = rollout 胜率/承伤·杀/击杀；行2 = eval 胜率+胜局耗时/胜局残血；
 * 行3 = 败局耗时/道具；除 eval 胜率外均 rollout 所有 iter 平均口径），上「标签+值」下「走势图」 */
.tc-trends{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;flex:1;min-width:300px}
.tc-tcell{display:flex;flex-direction:column;gap:2px;padding:4px 8px;background:var(--row-hover);border:1px solid var(--border);border-radius:var(--r-2);min-width:0}
.tc-tcell__hd{display:flex;align-items:baseline;justify-content:space-between;gap:var(--sp-2)}
.tc-tcell__lbl{color:var(--muted);font-weight:600;font-size:var(--fs-2);white-space:nowrap}
.tc-tcell .spark{width:100%;height:26px;display:block}
.tc-mtrend__val--g{color:var(--green)}
.tc-mtrend__val--y{color:var(--yellow)}
.tc-mtrend__val--r{color:var(--red)}

/* 顶栏阶段指示器 */
.tc-phase{display:inline-flex;align-items:center;gap:5px;padding:3px 12px;border-radius:999px;font-size:var(--fs-2);font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.tc-phase--rollout{background:var(--accent-bg);color:var(--accent)}
.tc-phase--ppo{background:var(--green-bg);color:var(--green)}
.tc-phase__icon{font-size:13px;line-height:1}
.tc-phase__label{letter-spacing:.3px}
.tc-phase__elapsed{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-weight:700}

/* hero 最新 6 轮完整指标（§367 UI：首页训练状态区直读最近趋势） */
.tc-hero__iters{flex:1 1 100%;min-width:0;margin-top:0;border-top:1px dashed var(--border);padding-top:6px;overflow-x:auto}
.tc-hero__iters-hd{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-3);font-size:var(--fs-2);color:var(--muted);font-weight:600;margin-bottom:2px}
.tc-hero__iters-toggle{border:none;background:none;color:var(--accent);font-size:var(--fs-3);cursor:pointer;padding:0 2px;line-height:1}
/* 标题 + 主行/eval toggle 左侧一组（「完整指标表 ›」保持右对齐） */
.tc-hero__iters-left{display:flex;align-items:center;gap:10px;min-width:0}

/* 正在训练的课程标签（课程 select 后高亮；脉冲绿点提醒查看课程 ≠ 训练课程） */
.tc-training-tag{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;
  background:var(--green-bg);color:var(--green);font-size:var(--fs-2);font-weight:700;white-space:nowrap}
.tc-training-tag .tc-dot{animation:tc-live 1.6s ease-in-out infinite}
.tc-hero__iters .tc-table thead th{position:static} /* 6 行紧凑表无需吸顶 */
.tc-hero__iters .tc-table tbody td{padding:3px 8px;font-size:var(--fs-2)}
.tc-hero__iters .tc-table thead th{padding:4px 8px}

/* 组件 4 小卡 */
/* 组件 chips 行（原 4 小卡 → 一行 chips，复用节点 pill 风格） */
.tc-comps{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);padding:6px 12px;margin-bottom:10px}
.tc-comps .lbl{font-size:var(--fs-2);color:var(--muted);margin-right:var(--sp-1)}
.tc-cc__name{font-size:var(--fs-2);font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* trainingLoop 运行模式高亮徽章 */
.tc-cc__mode{display:inline-block;margin-left:6px;padding:0 7px;border-radius:999px;background:var(--accent-bg);color:var(--accent);font-weight:700;font-size:10.5px;white-space:nowrap;line-height:18px;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace}
.tc-cc__acts{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.tc-cc__detail{display:block;background:var(--gray-bg);border:1px solid var(--border);border-radius:var(--r-1);padding:var(--sp-2) var(--sp-3);font-size:11px;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;color:var(--muted);white-space:pre-wrap;word-break:break-all;max-height:200px;overflow-y:auto;margin-bottom:var(--sp-4)}
.tc-cc__err-link{color:var(--red);font-weight:700;text-decoration:none}
.tc-cc__err-link:hover{text-decoration:underline}
.tc-cc__sec{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);word-break:break-all;min-width:0}
.tc-cc__sec code{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;background:var(--gray-bg);border-radius:6px;padding:1px 6px;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} /* code 铺满剩余宽、复制键贴右缘 */

/* 状态点 */
.tc-dot--warn{background:var(--yellow)}
.tc-dot--on{background:var(--green)}
.tc-dot--dead{background:var(--red)}
.tc-dot--empty{background:var(--border)}

/* Switch（节点启停 toggle，role=switch；内嵌轨道 28×16） */
.tc-switch{display:inline-flex;align-items:center;border:none;background:none;padding:2px;margin-left:2px;cursor:pointer;line-height:1}
.tc-switch:disabled{opacity:.45;cursor:not-allowed}
.tc-switch__track{display:inline-block;width:28px;height:16px;border-radius:999px;background:var(--gray-bg);border:1px solid var(--border);position:relative;transition:background .15s,border-color .15s}
.tc-switch__thumb{position:absolute;top:1px;left:1px;width:12px;height:12px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(16,24,40,.25);transition:left .15s}
.tc-switch--on .tc-switch__track{background:var(--green);border-color:var(--green)}
.tc-switch--on .tc-switch__thumb{left:13px}

/* 节点 pill 行 */
.tc-nodes{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);padding:6px 12px;margin-bottom:10px}
.tc-nodes .lbl{font-size:var(--fs-2);color:var(--muted);margin-right:var(--sp-1)}
.tc-npill{display:inline-flex;align-items:center;gap:var(--sp-2);border:1px solid var(--border);border-radius:999px;padding:3px 12px;font-size:var(--fs-2);background:var(--card);cursor:pointer;flex-shrink:0;user-select:none}
.tc-npill:hover{border-color:var(--accent)}
.tc-npill b{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-weight:600;font-size:var(--fs-2)}
.tc-npill .v{color:var(--green);font-weight:600;font-size:var(--fs-2)}
.tc-npill--off .v{color:var(--muted)}
/* 停用节点 pill：灰点（tc-dot--empty）+ 灰字，与离线红点区分（2026-09-11 用户指令） */
.tc-npill--off.tc-npill--disabled .v{color:var(--gray)}
/* 慢节点 pill：琥珀字（与 tc-dot--warn 同色系）——仍在贡献，非掉线 */
.tc-npill--off.tc-npill--slow .v{color:var(--yellow)}
.tc-npill--dead{opacity:.72}
.tc-npill--collapse{color:var(--accent);border-color:var(--accent);background:var(--accent-bg);font-weight:600}
.tc-npill__contrib{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-weight:700;font-size:var(--fs-2);color:var(--green);border-left:1px solid var(--border);padding-left:var(--sp-2);margin-left:2px}
.tc-npill__edit{display:flex;gap:6px;align-items:center}
.tc-npill__edit input[type=number]{width:56px;padding:2px 6px;border:1px solid var(--border);border-radius:6px;font-size:var(--fs-2)}
.tc-nodes .more{margin-left:auto;font-size:var(--fs-2);color:var(--accent);font-weight:600;border:none;background:none;cursor:pointer}
.tc-nodes .more:hover{text-decoration:underline}

/* 工具行（TrainingLoop 启动弹窗内） */
.tc-line{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}
.tc-line .tc-muted{font-size:var(--fs-2)}

/* 详情层（全屏 modal，用户 2026-09-07：抽屉太挤改全屏） */
.tc-drawer-mask{position:fixed;inset:0;background:rgba(16,24,40,.35);z-index:70}
.tc-drawer{position:fixed;top:4vh;left:4vw;right:4vw;bottom:4vh;background:var(--bg);border:1px solid var(--border);border-radius:var(--r-3);z-index:71;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(16,24,40,.3)}
.tc-drawer__hd{padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:var(--sp-2);background:var(--card)}
.tc-drawer__tabs{display:flex;gap:var(--sp-1);padding:var(--sp-2) var(--sp-4);border-bottom:1px solid var(--border);background:var(--card)}
.tc-drawer__tab{padding:3px 14px;border-radius:999px;font-size:var(--fs-2);color:var(--muted);cursor:pointer;border:none;background:none;font-weight:600}
.tc-drawer__tab--on{background:var(--accent);color:#fff}
.tc-drawer__body{flex:1;display:flex;flex-direction:column;gap:var(--sp-2);overflow:hidden;padding:var(--sp-3) var(--sp-4)} /* 页面不滚动——表格内部滚动 */
/* 抽屉面板：竖直弹性链 面板→表根→tablewrap，表格整高铺满 modal 中间区域 */
.tc-drawer__panel{display:flex;flex-direction:column;gap:var(--sp-3);flex:1;min-height:0}
.tc-drawer__panel .tc-dtable{flex:1;min-height:0}
.tc-drawer__panel .tc-tablewrap{flex:1 1 auto;min-height:0;max-height:none}

/* 弹窗（TrainingLoop 启动） */
.tc-modal-mask{position:fixed;inset:0;background:rgba(16,24,40,.35);z-index:80;display:flex;align-items:center;justify-content:center}
.tc-modal{background:var(--card);border:1px solid var(--border);border-radius:var(--r-3);box-shadow:0 8px 32px rgba(16,24,40,.18);width:min(440px,92vw);padding:var(--sp-4);display:grid;gap:var(--sp-3);max-height:86vh;overflow:auto}
.tc-modal h3{margin:0;font-size:var(--fs-4);font-weight:700}
.tc-modal__foot{display:flex;align-items:center;gap:var(--sp-3)}
.tc-modal__foot .sp{flex:1}

/* 复制 */
.tc-copy{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--border);border-radius:var(--r-1);padding:2px 8px;font-size:11px;color:var(--muted);cursor:pointer;background:var(--card);white-space:nowrap}
.tc-copy:hover{border-color:var(--accent);color:var(--accent)}
.tc-copy--done{color:var(--green);border-color:var(--green)}
.tc-copy--sm{padding:1px 6px;font-size:10.5px}
/* §361①：无字图标复制键（仅 ⧉/✓，复制语义走 title/aria）。 */
.tc-copy--icon{min-width:22px;padding:2px 6px;justify-content:center;font-size:12px}
/* §361⑤：local 本机直跑 pill（只读，不悬停高亮，无点击语义）。 */
.tc-npill--local{cursor:default;border-color:var(--accent);background:var(--accent-bg)}
.tc-npill--local:hover{border-color:var(--accent)}
.tc-npill--local .v{color:var(--muted)}
/* 局域网只读节点 pill：无点击语义、无 hover 高亮（编辑/冒烟仅本机） */
.tc-npill--ro{cursor:default;opacity:.92}
.tc-npill--ro:hover{border-color:var(--border)}
`
}

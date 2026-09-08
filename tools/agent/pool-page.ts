/** pool-page.ts — /pool 兼容壳（v3 过渡态：监控已并入训练控制台，勿再引 monitor/）。
 *
 *  远端 sampler-agent 的 GET /pool 走本文件的 mtime 键控动态 import（§341 语义）。
 *  monitor/ 已整体迁入 console（§3.4），故本文件改为自包含占位页——不再重导出
 *  已删除的 monitor 模块。完整下线（本文件 + sampler-agent /pool 路由一并删除 →
 *  请求 404）留待 P3.5 与 tools/agent 升级波同批执行（训练轮次间隙，用户已接受）。 */

export function renderPoolPage(_ctx?: unknown): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>节点池监控</title>
</head>
<body style="font-family:system-ui,'PingFang SC',sans-serif;padding:28px;color:#1c2333">
<h3>节点池监控已下线</h3>
<p>远端 /pool 页面已随训练控制台 Preact 化改造撤销（<code>plan/Training-Console-Preact.md</code> v3）。</p>
<p>请在主控机训练控制台查看节点池统计与控制：<b>bun run train</b> → <code>http://127.0.0.1:8900</code>（节点池卡「统计」视图，独立 /api/pool 慢节奏）。</p>
</body>
</html>`
}

export type PoolPageCtx = Record<string, never>

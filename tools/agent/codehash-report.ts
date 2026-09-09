/**
 * codehash-report.ts — 双侧 codeHash 诊断工具（F4，plan/dist-codehash-stale-fix.md）。
 *
 * 定位「节点被永久误判 stale」时唯一的双侧 diff 手段：在训练机跑
 *   python -c "import dist_common;print(dist_common.code_hash_report())" > local.tsv
 * 在节点跑
 *   bun tools/agent/codehash-report.ts > mac.tsv
 * 然后 `diff local.tsv mac.tsv`——多文件 / 少文件 / 内容不同一目了然。
 *
 * 输出 TSV：`sha8\tsize\trelPath`（按 relPath 排序），末行 `codeHash=<full>`。
 * 本文件不在 codehash-files.txt 集内（诊断工具，改动不触发升级波——同 pool-page.ts 约定）。
 */
import { codeHashReport } from './codehash-files'

console.log(codeHashReport())

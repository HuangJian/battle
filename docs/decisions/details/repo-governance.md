# 决策正文归档 — repo-governance

> 2026-09-23 从 `DECISIONS.md` 搬出的决策正文（索引行与编号仍在 `DECISIONS.md`）。

## §2026-09-08-decisions-governance（2026-09-08，用户拍板执行 plan/decisions-governance.md）

- **背景**：DECISIONS.md 膨胀至 3734 行、编号至 §384，并行分支撞号（§293/§354/§355/§361 各 2 条同号）、
  历次瘦身必输（写入成本 0、清理成本 100，三 agent 并行每月 +30 条）。实测定案为准入问题，非整理问题。
- **备选与否决**：更勤瘦身 —— 否，写入门槛为零时任何频率瘦身都是负收益；换编号格式（全局计数器加锁）—— 否，
  顺序整数是全局共享可变计数器，并发必然撞号；ADR 分片（一决策一文件，P4）—— 暂缓，瓶颈在准入而非文件组织，
  收紧准入后写入量掉 80%，单文件 append 够用。
- **决定**：收紧准入（三问闸门 + 三类路由，禁双写）+ 防冲突命名（日期 ID `§YYYY-MM-DD-<branch>-<slug>`，
  旧编号 §1–§384 冻结契约不动）+ union 合并（`.gitattributes`）+ 只追加（新条目只写末尾）+
  模板外置（`docs/decisions/HOW-TO-ADD.md`）+ 自动校验（`tools/check-decisions.ts` 接进 `bun run check`，
  基线 `tools/decisions-baseline.json`）+ 一次性瘦身（编号集合不变式：只删正文不删编号；
  实验/调优 → progress 指针，bugfix/UI → commit 承载）。本次迁移 3734 → 约 980 行，编号集合逐条核对未变。
- **违反后果**：撞号与膨胀回归（目标 ≤5 条/月 vs 现状 ~30）、合并冲突复现、外部引用断链。
- **留存**：旧全文在 ccf49ff^（瘦身前全文版，git 历史为准，不另存本机备份）。


---

## §2026-09-15-sandbox-precommit-immunity（2026-09-15，用户指令：杜绝编码 agent 删除保护沙箱反复拦截 pre-commit / 反复弹删除审批；hook 进程树级免疫 + 零删除纪律）

- **背景**：WorkBuddy / Mimo 类编码 agent 自带 python 运行时 + 删除保护沙箱，经两个载体注入：
  **bash 函数**（BASH_ENV 指向 safe-delete-bash-env.sh → 每个**子** bash 启动时把 rm/unlink/rmdir
  重新包装成函数，带每回合删除配额 ~50）与 **python sitecustomize**（守卫 os.remove/shutil.rmtree）。
  后果：mypy 自清理 / pytest 临时目录被塞 SystemExit → INTERNAL ERROR 假红；hook EXIT trap 的
  rm 撞配额 FAIL_CLOSED → 门禁全绿提交却被 git 静默丢弃（2026-09-14 实测 count=181/182；
  2026-09-15 Mimo 再犯：反复重试 commit + 反复弹删除审批）。旧防御只在本 shell `unset -f rm`——
  子进程经 BASH_ENV 重新注入后照拦不误；python 侧开关只写进注释让「调用方设」= 等于不设。
- **决定（三管齐下，只对本 hook 子树生效，不碰宿主环境）**：检测到注入载体（名字含
  CODEBUDDY/SAFE_/BUDDY/SANDBOX 的环境变量，或 BASH_ENV/ENV 含 safe/delete/buddy/runtime）即拉闸：
  ① BASH_ENV/ENV 指向 /dev/null + unset 全部注入变量 → 子 bash 拿不到包装函数与配额配置，
  删除走真 `rm`（配额彻底失效）；② CODEBUDDY_SAFE_DELETE_ENABLED=0 + PYTHONNOUSERSITE=1 →
  python 侧 sitecustomize 守卫对整棵子树（mypy/pytest/ruff 及其自清理）整体关闭；
  ③ PATH 滤掉命中 safe-delete/safe-bin/codebuddy/shell-runtime 的目录 → 直接可执行 shim 也无处可寻。
  **铁律追加（hook 零删除纪律）**：hook 自带临时文件清理一律「截断不删除」（`: > file`），
  删除 syscall 归零——截断是写不是删，任何删除拦截都对其失效；残留 0 字节文件在 gitignored
  tmp/ 由 periodic 清理兜底。权威文档 = `tools/githook/pre-commit` 顶部注释。
- **被否决的备选**：① v1「本 shell unset -f」—— 已被实测证伪（子进程重注入）；②「exec 重执行 +
  env -u 剥离」—— 引号/递归复杂度不值，BASH_ENV=/dev/null 更简单且等效；③「让调用方自己设
  CODEBUDDY_SAFE_DELETE_ENABLED=0」—— 调用方正是肇事 agent，指望它们设等于不设。
- **违反后果**：hook 再出现删除 syscall（trap/selftest 里的 rm 等）或依赖 agent 自设开关 ⇒
  沙箱弹审批/配额重演、agent 反复重试 commit（本次要消灭的循环）；child 进程再被 BASH_ENV
  注入 ⇒ mypy/pytest 假红回归。
- **验证**：hook `--selftest` 绿；伪造 BASH_ENV 注入文件（真定义 rm() 包装函数并 export -f）+
  CODEBUDDY 标记变量跑 selftest → 拉闸行出现且三层子进程（self/child/grandchild）rm 均为真身、
  载体变量已清、真实删除可用（无配额/无审批）；根 `bun run check` **1825 pass / 3 skip / 0 fail**。
- **追加（2026-09-15，用户提问「coding agent 直接跑 python.exe -m pytest xxx 也会被拦截吗」
  ——**会**）——把 python 入口也一起处理**：删除拦截发生在 **python 进程内部**
  （os.remove / shutil.rmtree / pathlib.unlink 层，由启动时加载的 sitecustomize 守卫实现），
  bash 的 rm() 包装覆盖不到——`python -m pytest` 的 tmp_path 清理、测试内删除一删一个准
  （本机实测：两个解释器 site-packages 都**没有**烘焙 sitecustomize，注入是**环境变量驱动**的，
  沙箱 env 随进程树带下来才激活，所以整树净化可治）。免疫做成三件套：① 共享实现
  `tools/githook/_sandbox-sanitize.sh`（**唯一事实源**，pre-commit 改为 source 它，禁止复制粘贴漂移；
  幂等 SANDBOX_SANITIZED 守卫）；② 新增 python 启动器 `tools/githook/nn-py-safe.sh` ——
  **先拉闸、再 exec 指定解释器**（默认 nn-training/.venv，NN_PY 可覆盖），语义与裸
  `python.exe …` 逐字节一致，agent 跑 pytest/任意 python 一律经它；
  ③ `CODEBUDDY_SAFE_DELETE_ENABLED=0` + `PYTHONNOUSERSITE=1` 改**无条件设**（惰性变量，
  普通机器无害）——不再依赖「调用方自觉设」。**验证口径**：bash 层三层免疫在注入模拟下
  逐层为真（self/child/grandchild rm 均为真身、变量已清）；python 内部 env 在本验证宿主
  （bash→Windows exe 边界剥环境）无法直视，靠「沙箱 env 能传进去就能传开关出来」+ 既有
  `CODEBUDDY_SAFE_DELETE_ENABLED` 文档语义成立。**违反后果**：agent 再裸跑 `python -m pytest`
  ⇒ pytest 假红/弹审批重演；有人在别处复制粘贴拉闸逻辑 ⇒ 二处漂移，任一失效用户都得重推一次。


---

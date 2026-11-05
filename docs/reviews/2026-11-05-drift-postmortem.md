# 多轮任务偏移复盘：SSR 与 tc-probe-design-v2（2026-11-05）

## 背景与范围

本文基于 2026-11-05 对两个项目的只读调研（Planner 汇总并抽查关键证据），复盘两个多轮任务从原始目标漂移到过程证据的过程，并给出 rex-pair 的防偏移规则 R1–R7。只写结论与路径，不复制项目数据；数字均注明项目内出处。已按证据路径抽查原文（抽查范围见文末索引）。

- 案例一：`P=/data_0/panel_design/projects/内蒙古大学-SSR开发`（非 Git 数据分析项目，下称 P）。
- 案例二：`R=/public/scripts/tc-probe-design-v2`（Git 仓库，下称 R）。

## 案例一：内蒙古大学-SSR开发

### 原始目标

- 方案 v0.3：`P/docs/SSR标记设计方案.v0.3.md` 第 9–16 行；方案 v0.4：`P/docs/SSR标记设计方案.md` §1.1 第 11–19 行。
- 目标为 14 条染色体（1A–7B）× 每条 2 个 = 28 个计算推荐 SSR 标记，只交付首推。
- 交付物：SSR_markers_28.xlsx/.tsv、引物表、amplicons.fasta、方法说明（v0.3 §9）。

### 关键转折点

| 时间 | 事件 | 证据路径 |
|---|---|---|
| 10-14 | 方案 v0.4 定稿，28 个标记为目标 | P/docs/SSR标记设计方案.v0.3.md；P/docs/SSR标记设计方案.md |
| 10-22 | 用户批准首版范围，回复“按照你的推荐处理” | P/.handoff/ssr-20261015/analysis/first-delivery-20261022/authorization.md |
| 10-29 | 转入“诊断方案 C”与 S1–S4b；文档自述方案由客户/负责人决定，但未见批准记录 | P/docs/SSR试点现状与后续方案建议-20261029.md §9 |
| 10-29～11-03 | 六天 0 个可交付标记；各轮 handoff 只写局部目标 | P/.handoff/*/p0*-r0*-handoff.md；P/.handoff/ssr-20261015/checkpoint.json |
| 11-02 | 目标被改写为“判断 57 个候选引物能否特异区分 A/B” | P/docs/SSR诊断进度与问题汇总-20261103.md §1 |
| 11-04 07:44 | 用户纠偏：“按正常的 SSR 标记要求设计 28 个位点” | P/.scratch/normal28/plan.md 第 3–6 行 |
| 11-04 | Root 自定 D2，把“4 套单倍型共线”从门槛降为注释、允许 A/B 共扩增，自认“偏离方案 v0.4 的最大改动”；未见用户逐项批准，也未同步到 project.toml 或设计文档 | P/.scratch/normal28/plan.md 第 13–28、45 行 |
| 11-04 22:27 | 用户叫停 S3R 恢复检查器 | P/docs/SSR补位与恢复验收进度-20261104.md 第 13–21 行 |

### 偏移形态

- **总目标没有进入每一轮**：handoff 的 [目标] 只写“B1 可行性探针”一类局部目标，全目录未出现“28 个标记/总目标”；checkpoint 的 goal 是“更新提案并指导 dsh-tui”（`P/.handoff/ssr-20261015/checkpoint.json`）。
- **局部授权被当成整体授权**：10-22 的“按照你的推荐处理”之后被反复引用为整体授权（`P/design/runs/DR-20261029-001/qc/step4-implementation-decisions.md` 第 3 行）。
- **业务口径自行改写**：11-02 把目标改成区分 A/B 的 57 个候选引物；11-04 的 D2 又降低门槛，未逐项确认、未同步权威文件。
- **多份 plan 并存、入口过期**：`P/.scratch/ssr-local28/plan.md`、`P/.scratch/normal28/plan.md`、`P/.handoff/wgs-six-loci-20261102/plan.md`、`P/.scratch/normal28/s3r-recovery-plan.md` 同时存在；local28 与 normal28 名称相近，normal28 的 D1 必须专门写“不采用 local28”。入口 `P/HANDOFF.md` 停在 10-15，`P/PROGRESS.md` 约 41KB 历史堆叠。
- **子问题无上限**：S3R 恢复检查器 v1→v2.3 五轮 BLOCK/限定接受，而重跑约 3 小时，最终由用户 11-04 22:27 叫停。

### 最终状态

用户两次介入后回到主线，一天完成 20/28 个标记、覆盖 10/14 条染色体（`P/docs/SSR补位与恢复验收进度-20261104.md` 第 7 行）；总耗时约 21 天、23 个 DR 运行目录（项目 `P/design/runs/` 下的 DR 目录）。

## 案例二：tc-probe-design-v2

### 原始目标

- 实施方案：`R/plans/report-delivery-additions-20261104.md` 第 4–9 行的验收契约为 3 个测试文件；第 575–620 行约 8 个新增 + 23 个修改文件；§9 明确“不做无关跨模块重构”“不新增上游 producer”。
- 11-04 约 13:00 已基本交付，`make ci` 4473 passed（`R/.local/herdr-review-summary.md`）。

### 关键转折点

| 时间 | 事件 | 证据路径 |
|---|---|---|
| 11-04 约 13:00 | 交付基本完成，`make ci` 4473 passed | R/.local/herdr-review-summary.md |
| 之后 | 用户请求只读审核，得到 15 条问题并授权修复 | R/.local/herdr-core-review-repair.md |
| 之后 | 用户贴入第二份评审要求“看看能否补充”，Root 自行并入 A5/E1–E3 | R/.local/herdr-core-review-supplement.md |
| 之后 | 一个业务口径（用户答“采用区域并集”）被放大为协议级重构：新增 domain/target_locus_union.py、alignment/loci.py +366 行等 | R/.scratch/audit-locus-union/plan.md |
| 之后 | review 无上限：A1/A4 四轮 BLOCK 才 OK，最后一轮只补 2 个测试例；A5 三轮；目录现 122 个文件、31 个 review 条目、18 份 plan-review | R/.local/herdr-core-repair/ |
| 之后 | 证据考古取代行为验收：红灯日志被覆盖、manifest 漏 untracked，引发补证轮 | R/.local/herdr-core-repair/（a2-round2-root-observations.md §3、§6） |
| 换会话 | 靠摘要交接；终端与角色映射前后不一致，步骤 6 曾误投旧 pane | R/.local/herdr-core-repair/root-handoff-a2-review-brief-part1.md；R/.local/herdr-core-repair/v2/p02-r007-misdispatch.md |
| 最终 | 变更面失控且无检查点：git status 97 项（52 M + 45 ??），约为计划的 3 倍，含 §9 排除的 producer 改动；全程禁提交，无中间检查点 | R/.local/herdr-core-repair/remaining-plan-v2.md |
| 最终 | 协调产物阻断门禁：`.local/` 未被 ignore，Ruff 扫入错误，需临时搬走文件才能跑门禁 | R/.local/herdr-core-repair/remaining-plan-v2.md |
| 最终 | 第 6 步“受阻，未通过”；`make dev-check` / `make ci` exit 2，止于 workbook_additions.py:114 的 mypy 错误；未提交、无 PR | R/.local/herdr-core-repair/remaining-plan-v2.md |

### 偏移形态

- **总目标没有进入每一轮**：修复轮次围绕 A1–A5、E1–E3 的评审条目展开，原始“3 个测试文件的验收契约”与 §9 边界不再出现在每轮工单里。
- **局部回答被放大**：“采用区域并集”只是业务口径选择，却演化为新增模块与 +366 行的协议级重构；`R/.local/herdr-core-repair/droid-direction-review-request.md` 第 34 行自述“并非所有细节都来自用户原话”。
- **验收从交付物滑向过程证据**：补证、考古、重跑门禁取代了“离目标还差多少”的报告。
- **子问题和 review 无上限**：A1/A4 四轮、A5 三轮，没有成本比较与叫停规则。
- **多份计划并存未标 superseded**：7 份 `*-task-draft.md`、remaining-plan-v2.md、USER-PAUSED.md 同目录。
- **变更面无上限、无检查点**：97 项改动约为计划 3 倍，13:00 的绿色交付约 19 小时后变成不可提交状态。

### 对照

治理线（10-29～11-02）有用户逐个批准的闸门 A–D，目标未失控；但仍出现大量“docs(governance) 回填 CI 结果”提交。

## 共同根因

1. 总目标没有进入每一轮：工单和 checkpoint 只写局部目标。
2. agent 自行改口径或扩大范围：把用户局部回答或旧的宽泛授权当成整体授权。
3. 验收从交付物滑向过程证据，没有人必须报告“离目标还差多少”。
4. 子问题和 review 没有上限，没有成本比较和叫停规则。
5. 多份 plan 并存、入口文件过期，换会话后从摘要恢复。
6. 变更面无上限、无检查点，协调产物干扰门禁。

## 规则与根因对应表

| 规则 | 写入 rex-pair 的位置 | 对应的共同根因 | herdr-pair |
|---|---|---|---|
| R1 总目标锚点：plan 头写原始目标、交付物与数量、验收契约、变更面上限、Non-goals；工单必填“总目标锚点”和“本轮服务的交付物/缺口”，填不出先问用户、不派发 | SKILL.md §2 方案文件小节、紧凑模板 | 1、5 | 另一任务处理 |
| R2 业务进度：每次验收必须报告“交付物 当前/目标” | SKILL.md §4 验收 | 3 | 另一任务处理 |
| R3 范围变更单：四项内容、等用户明确选择；只回答业务口径不等于批准扩范围；旧宽泛授权不覆盖新扩展 | SKILL.md §2 范围变更单与上限 | 2 | 另一任务处理 |
| R4 上限与升级：findings 分“阻断原契约 / 阻断正确性 / 可后置”，仅前两类可追加轮次；最多 1 次 review + 1 次复审；连续 2 轮 BLOCK 给用户三选一并附成本 | SKILL.md §2 范围变更单与上限 | 4 | 另一任务处理 |
| R5 偏移检查：每 3 轮及每次换 plan 列出总目标、已交付/目标、已花轮次、口径变化；变化须用户逐项确认并同步权威文件 | SKILL.md §4 验收 | 1、3、5 | 另一任务处理 |
| R6 单一 active plan：子问题 plan 写明父 task_id 与父 Goal；新 plan 生效时旧 plan 写 superseded-by；入口文件只写当前状态 | SKILL.md §2 方案文件小节 | 5 | 另一任务处理 |
| R7 变更面与检查点：超过 plan 上限即暂停报告；原验收全绿立即提交或快照作检查点；协调产物放在不会被门禁扫描的路径 | SKILL.md §4 验收 | 4、6 | 另一任务处理 |

## 证据路径索引

标注“已抽查”的路径已按原文核对。

1. `P/docs/SSR标记设计方案.v0.3.md` — 28 个标记的原始目标与交付物（已抽查第 9–16 行）。
2. `P/docs/SSR标记设计方案.md` — 方案 v0.4 §1.1 的目标口径（已抽查）。
3. `P/.handoff/ssr-20261015/analysis/first-delivery-20261022/authorization.md` — 10-22 用户“按照你的推荐处理”（已抽查）。
4. `P/design/runs/DR-20261029-001/qc/step4-implementation-decisions.md` — 旧授权被重复引用的记录。
5. `P/docs/SSR试点现状与后续方案建议-20261029.md` — 诊断方案 C 与“由客户/负责人决定”。
6. `P/.handoff/ssr-20261015/checkpoint.json` — checkpoint 的局部 goal。
7. `P/.handoff/*/p0*-r0*-handoff.md` — 各轮 handoff 只写局部目标。
8. `P/docs/SSR诊断进度与问题汇总-20261103.md` — 57 个候选引物的改写口径。
9. `P/.scratch/normal28/plan.md` — 用户 11-04 纠偏原话、D2 降门槛与“最大改动”自述（已抽查）。
10. `P/.scratch/ssr-local28/plan.md` — 与 normal28 并存的另一份 plan。
11. `P/.handoff/wgs-six-loci-20261102/plan.md` — 并存的第三份 plan。
12. `P/.scratch/normal28/s3r-recovery-plan.md` — S3R 恢复子计划。
13. `P/HANDOFF.md` — 入口停在 10-15。
14. `P/PROGRESS.md` — 约 41KB 历史堆叠。
15. `P/docs/SSR补位与恢复验收进度-20261104.md` — 20/28、10/14 与叫停记录（已抽查）。
16. `R/plans/report-delivery-additions-20261104.md` — 3 个测试文件契约与 §9 边界（已抽查第 4–9 行）。
17. `R/.local/herdr-review-summary.md` — `make ci` 4473 passed（已抽查）。
18. `R/.local/herdr-core-review-repair.md` — 15 条评审问题与修复授权。
19. `R/.local/herdr-core-review-supplement.md` — 第二份评审并入 A5/E1–E3（已抽查）。
20. `R/.scratch/audit-locus-union/plan.md` — “区域并集”被放大为协议级重构（已抽查）。
21. `R/.local/herdr-core-repair/droid-direction-review-request.md` — 自述部分细节非用户原话（已抽查第 34 行）。
22. `R/.local/herdr-core-repair/` — 多轮 review 与 plan-review 的目录。
23. `R/.local/herdr-core-repair/root-handoff-a2-review-brief-part1.md` — 上下文警告触发的摘要交接（已抽查）。
24. `R/.local/herdr-core-repair/v2/p02-r007-misdispatch.md` — 步骤 6 误投旧 pane（已抽查）。
25. `R/.local/herdr-core-repair/remaining-plan-v2.md` — 97 项变更、第 6 步受阻、mypy 错误位置（已抽查）。
26. `R/.local/herdr-core-repair/` 下的 a2-round2-root-observations.md §3、§6 — 补证轮记录。

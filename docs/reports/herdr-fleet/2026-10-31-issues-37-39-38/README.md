# herdr-fleet：#37 / #39 / #38 实施、验收与问题记录

三个原独立 PR 已按 #40 → #41 → #42 合并；使用中发现的 A/B/C 形成的后续 PR #43/#44/#45 在完成验收后，Root 选择按 #43 → #44 → #45 顺序继续合并（Root 的执行选择，均无冲突）。六个 PR 均已 MERGED，main 功能验收基线为 `12783ac14b4e23ce65dd5ac890acee4b894097b2`（#45 合并提交），各 PR 合并前或最终 HEAD 的 Python 3.11 / 3.13 CI 均通过。任务 worktree 与原现场证据保留。

## 用户授权与执行方式

- 使用 `skills/herdr-fleet` 处理 #37 / #39 / #38；任务 agent 为 Devin，参数 `--permission-mode dangerous`。
- 独立小 PR，执行顺序 #37 → #39 → #38；#38 策略为 `superseded` 保留审计记录，同时保留旧 blocked 并注明轮次与当前状态。
- 用户同意补充的标记边界、JSON 结构校验、旧事件迟入箱与投递竞争测试要求。
- 用户要求记录使用中发现的 skill 问题；本报告为本地记录，未发布新的 GitHub issue。
- Root 负责拆分、只读 diff/日志复核、真实 status 查询和证据归档；实现与测试由 Devin 执行。
- Root 决定增加一份本地组合验收包，原因是三个独立 PR 触碰共享文件且静态合并检查确认有冲突；未改变原 PR 的独立性。
- 对用户「处理本报告」的执行选择：Root 将 A 落实为启动/恢复操作指引、B 落实为测试隔离修复、C 先复现实验再做具体锁修复；这三个后续 PR 是 Root 的任务拆分，当时未自动合并，验收后经 Root 执行选择并入 main，未把该选择扩写为用户规则。

## 环境

- 主 checkout：`/home/glx/scripts/tc-skills`。
- 复用 fleet root：`/home/glx/scripts/tc-skills-fleet`；配置 repos.tcskills 指向 `../tc-skills-wt/main`，与主 checkout 共用 git common dir，base `origin/main`。
- `HERDR_ENV=1`，coordinator `wE:p1`；Herdr 0.9.1，插件 tc.herdr-fleet enabled，Devin integration current v2。
- 初始及各次派发后 doctor 均通过。doctor 仅验证基础环境，不证明 prompt 已被执行。
- 三个 issue 均已标 `ready-for-agent`，#37 移除 `needs-triage`。
- 保留已有 `2026-10-30-devin-perf-smoke/` 未提交现场证据，本轮没有将其加入 PR。

## 原独立交付（合入其他 PR 前的历史验收）

| Issue | PR | 最终 HEAD | 完整隔离测试 | CI |
| --- | --- | --- | --- | --- |
| #37 回报摘取 | https://github.com/tcuni24/tc-skills/pull/40 | `11c01deebd2b9812b4eb1ff4c94099bdc1f41f03` | 105 passed，exit 0 | Python 3.11 / 3.13 SUCCESS |
| #39 PR 查询三态 | https://github.com/tcuni24/tc-skills/pull/41 | `4bcd7a4f41a56efdb6b49bccca53cbf6a02826e5` | 110 passed，exit 0 | Python 3.11 / 3.13 SUCCESS |
| #38 过期轮次通知 | https://github.com/tcuni24/tc-skills/pull/42 | `5f25a24a9be9506492348d0afb549519a60d51f8` | 100 passed，exit 0 | Python 3.11 / 3.13 SUCCESS |

合并前独立验收时的远端状态与 CI 原始 JSON：`pr-40-final.json`、`pr-41-final.json`、`pr-42-final.json`；其中 OPEN 为历史状态，当前合并状态见下一节。

完整验收命令为 `python -m pytest tests/herdr-fleet/test_fleet.py -q`。运行环境使用任务 #37 的只读 `.venv/bin/python`；各最终 HEAD 通过 `git archive` 导出到 `/project/tmp/*-snapshot`，祖先无真实 fleet 配置。Root 已逐项比对快照全部 tracked 文件与对应 PR HEAD 内容一致，读取完整日志并检查 PR diff。三个 PR 的 `git diff origin/main...HEAD --check` 均通过。

### #37：回报摘取

- 行首允许空格/Tab，严格标记 token 边界，支持折叠文本中的 END 子串，必须闭合并返回最后完整块。
- dedent 保留字段自身子级缩进；文档和启动提示建议 fenced text，并声明不恢复 Markdown 折叠语义。
- 两份真实终端 JSON 裁剪 fixture 已提交。
- Root 复核首版发现 `run().strip()` 会破坏输出第一行即缩进标记的 dedent；同一 Devin 补修为局部 `agent_output()` 读取路径保留首行空白，并增加无前文测试。
- Root 用 PR 版本脚本对真实 Devin 终端执行 status，exit 0、report 非空、`^结果：` 行首匹配成功，原始证据 `report-block-37-fixed-live-status.json`。
- 完整隔离日志：`/project/tmp/report-block-37-snapshot-pytest.log`（105 passed / EXIT=0）。

### #39：PR 查询三态

- `pr_info` 区分 found / none / unknown；none 仅限成功空数组，执行错误、超时、JSON/结构错误均 unknown。
- JSON 新增 pr_state/pr_error，保留原 pr 对象契约；文本区分无/未知；unknown 时人工核查 PR；通知保留 `?`，包含 found+unknown 的多仓库情况。
- Root 复核首版发现只校验键存在，没有校验值类型；同一 Devin 补修 number 正整数且排除 bool、url/state 非空字符串，增加 status 与通知 CLI 测试。
- Root 用 PR 版本脚本执行真实 GitHub 查询，exit 0、pr_state=found、PR=41；证据 `pr-state-39-fixed-live-status.json`。未实测真实 Gitee provider。
- 完整隔离日志：`/project/tmp/pr-state-39-snapshot-pytest.log`（110 passed）；退出码和命令见任务 `pr-state-39/evidence/commands.md`。

### #38：旧终态与 blocked

- 每条事件保留产生时的 baseline；旧记录可从 dedup_key 第三项读取，无法解析时注明未知，不能反推真实历史。
- 新轮次确认后，同任务更早 pending done/idle 标 superseded，历史保留、attempts 不变；deliver 按 TASK 再复核。失败 send 不取代，其他任务不受影响。
- 旧 blocked 保留，标记旧→当前轮次及当前状态，phase 使用构造时采样的实时状态。
- Root 复核首版发现：消息构造期间新 send 推进轮次时，blocked 仍 pending，可能沿用旧标注；同一 Devin 增加 round_snapshot 锁内复核，漂移则锁外重建，新增真实 CLI + gh barrier 测试，确认只投递一条含新轮次/working 的 blocked。
- 投递锁→排序任务锁→fleet state 的锁序保持一致；复核与 prompt 在同组任务锁内，昂贵消息构造在锁外。文档明确现存 task_event 的 in-lock agent get 不属于本次改动。
- 完整隔离日志：`/project/tmp/stale-round-38-snapshot-pytest.log`（100 passed，112.66s，agent 报告 exit 0）；Root 比对快照与 HEAD 内容一致。
- #38 的事件/并发行为由 fake-herdr CLI 测试验证，未做真实 Herdr 端到端复验。状态采样到 prompt 的时间差、coordinator 状态检查到 prompt 的非原子窗口仍在文档中说明。

## 本地组合验收

Root 静态 `git merge-tree --write-tree` 检查（没有移动分支或合并远端）：

- #40 + #41：SKILL.md、test_fleet.py 有文本冲突。
- #41 + #42：fleet.py 有文本冲突。
- #40 + #42：可自动合并。

原始输出见 `merge-40-41.txt`、`merge-41-42.txt`、`merge-40-42.txt`。这不能表述为三个 PR 可无冲突直接合并。

Devin 随后在独立 clone `/project/tmp/fleet-37-39-38-combined` 中，按 #40 → #41 → #42 顺序应用六个提交，解决上述三处冲突：同时保留文档和全部测试，notice_text 同时保留三态 PR 的 `?` 与轮次/实时状态逻辑。没有适配、删除或跳过任何测试。

- 完整测试：**133 passed in 156.67s，exit 0**。
- `git diff --check 957b910..HEAD`：exit 0。
- 组合 HEAD：`85f585e75e005c8c88726191eac939980850d96b`。
- 组合 tree：`6f5fbf0b1001dadd702460f40ef084a28ba6850c`。
- Root 已读取合并后 notice_text 代码和完整 pytest 日志，核对原 PR HEAD 未变、所有原 worktree 的 tracked 文件无 diff。
- Root 核对 `combined.patch` 与该组合仓库 `git format-patch --stdout 957b910..HEAD` 的字节完全一致；SHA256 `f96f9d6280e01736b76577b6670f13806571990497a3074ac28fdce4ef3e525b`。未另行执行 git am 恢复测试。
- 本地组合验证同样采用 fake-herdr，未做组合版本的真机端到端验证。

归档：

- [组合说明](combined/README.md)
- [完整测试日志](combined/pytest.log)
- [六提交组合补丁](combined/combined.patch)

原材料最初位于 `worktrees/stale-round-38/tc-skills/evidence/combined/`，仅有三个 untracked 验收文件。Root 已复制到本报告；#42 更新 main 时，Devin 又将原件移到任务仓库外的 `worktrees/stale-round-38/evidence/combined/`，仓库恢复干净。材料未丢弃或混入实现提交。

## 实际合并验收

用户随后授权先合并三个独立 PR，再处理本报告。采用普通 merge，逐次以 `--match-head-commit` 核对已复核 HEAD；未 force push。

| PR | 合并前复核 HEAD | 完整隔离测试 | 合并提交 | 合并时间（UTC） |
| --- | --- | --- | --- | --- |
| [#40](https://github.com/tcuni24/tc-skills/pull/40) | `11c01deebd2b9812b4eb1ff4c94099bdc1f41f03` | 105 passed | `b04851b5cf1bda7267d7fd50ef4616fb45358bf4` | 2026-10-31 01:55:04 |
| [#41](https://github.com/tcuni24/tc-skills/pull/41) | `ab980fb54dd56a4354a9802706ea8726b60d01c2` | 124 passed | `d74103d74a5d174b7615a927ff515f4a141d8a96` | 2026-10-31 02:02:37 |
| [#42](https://github.com/tcuni24/tc-skills/pull/42) | `98831bf4613b7110ff876e5e5f36148bb55d0781` | 133 passed | `468b1a895591441fb178b4b7ad1efd8228d7c55b` | 2026-10-31 02:13:36 |

- #41 和 #42 分别由原 Devin 将最新 main 普通 merge 回原分支，解决共享文件冲突后重新跑完整测试；Python 3.11 / 3.13 CI 均成功才合并。
- Root 已比对更新后各精确 HEAD 的全部 tracked 文件与隔离测试快照，读取完整日志，复核 diff。#42 与先前组合验收树仅有 `report_fixture` 辅助函数位置差异，仍先于其 parametrize 调用；生产代码一致。
- 新合并元数据：`pr-40-merged.json`、`pr-41-merged.json`、`pr-42-merged.json`。GitHub 随合并自动关闭了 #37、#39、#38；Root 已查询确认均为 CLOSED，没有另发关闭命令。
- 更新分支后的验收材料：[#41 日志与命令](merged/pr-41/commands.md)、[#42 验收说明](merged/pr-42/README.md)。#42 真实 worktree 仍为 132 passed / 1 failed（B），同 HEAD 无配置祖先快照为 133 passed / exit 0；未将两种结果混称为通过。
- 这些测试使用 fake-herdr；没有新增真实 Herdr 端到端并发验证。

三个原独立 PR 合并后，Root 选择继续合并三个已验收的后续 PR（Root 的执行选择），顺序 #43 → #44 → #45，普通 merge 均无冲突：

| PR | 最终 HEAD | 合并提交 | 合并时间（UTC） |
| --- | --- | --- | --- |
| [#43](https://github.com/tcuni24/tc-skills/pull/43) | `7c180c4535d4f524e3516016c9ed17431e63ce66` | `16f3ac7807feb3ededf6494ea8595ecad26e494c` | 2026-10-31 03:43:35 |
| [#44](https://github.com/tcuni24/tc-skills/pull/44) | `dc5d61ba6bd2d720dbda1c02b1ed28b8da363d04` | `c927ac4028c6ca0aa22bd05bb7f373022ed8f165` | 2026-10-31 03:43:45 |
| [#45](https://github.com/tcuni24/tc-skills/pull/45) | `d803c9dbd8d56500266fb8ef013120a6cf2cceba` | `12783ac14b4e23ce65dd5ac890acee4b894097b2` | 2026-10-31 03:43:55 |

- 三个来源 PR 最终 HEAD 的 Python 3.11 / 3.13 CI 均已 SUCCESS 后合并。
- 合并元数据：`followups/devin-startup-guide/pr-43-merged.json`、`followups/config-test-isolation/pr-44-merged.json`、`followups/event-query-lock/pr-45-merged.json`；各目录早期 `pr-43.json`、`pr-44.json`、`pr-45-code.json`、`pr-45-final.json` 中的 OPEN 为合并前历史状态，按原样保留。
- #45 合并提交 `12783ac1` 即本次归档任务的 base（origin/main）。

## 使用中发现的 skill / 集成问题

### A. Devin 信任页被当成可交互，首次任务提交可能超时

- #37：`fleet new --prompt ...` exit 1，错误为 `开始确认失败 / timeout / timed out waiting for agent status`。tab/worktree 保留，idle、seq9363 未变、无任务执行输出。恢复到同一会话后 fleet send exit 0，随后 working。
- #39：先 new 不带 prompt，再检查 interactive_ready=true，但 agent_session=null；第一次 send 仍超时。稍后出现会话 cool-rumba，idle、seq9376 未变、终端仅启动页；当时未观察到任务执行后恢复 send 成功。这是历史操作记录，缺少执行痕迹本身不能证明原请求未被接受或排队。
- #38：未提交 prompt 时等待35秒，interactive_ready=true/idle，但无 agent_session；peek 明确显示「Do you trust the authors of this directory? / Yes, trust / No, exit」。这是已确认的信任页与正式输入页区分失败现场。确认该授权任务目录的信任选择后，会话身份出现，首次任务提交成功。
- 结论：interactive_ready=true 单独不足以确认此版本 Devin 进入任务输入页。前两次的输入是否被信任页消耗，未捕获完整处理链路，仍不能确定；额度不是已证实原因，用户明确说明有额外额度。
- 可改进方向：skill/集成在启动时区分信任选择与正式输入页，再提交任务；提供提交不确定状态下不重复执行的恢复指引。
- 证据：`report-block-37-startup-status.json`、`startup-ls.json`、`startup-doctor.txt`、`recovery-send.txt`、`pr-state-39-submit.txt`、`pr-state-39-recovery.txt`、`stale-round-38-trust-screen.txt`、`stale-round-38-submit.txt`。
- 已交付操作指引 [PR #43](https://github.com/tcuni24/tc-skills/pull/43)，HEAD `7c180c4535d4f524e3516016c9ed17431e63ce66`，已 MERGED（合并提交 `16f3ac7807feb3ededf6494ea8595ecad26e494c`）。仅改 SKILL.md 与 workflow.md，限定 Herdr 0.9.1 / Devin 集成 v2 的已测行为：新目录先不带 prompt 创建，peek 核对实际屏幕和授权目录，处理明确的信任选择后再核查正式输入页并提交。
- Root 复核首版要求收紧两处边界，同一 Devin 已修正：信任页误 ready 与原超时分别记录；即使 seq 未推进、无执行输出、无产物三项同时成立，也不能确认未投递。没有明确未被接受或不会再执行的证据时保持不确定，不重发。
- 文档 diff、命令用法与节名已复核，diff --check 通过；没有为纯文档修改新增测试。Python 3.11 / 3.13 CI 均 SUCCESS。上游就绪检测仍未修复，本项交付为操作规避指引，已随 #43 合入 main。
- 合并元数据 `followups/devin-startup-guide/pr-43-merged.json`；同目录 `pr-43.json` 的 OPEN 为合并前历史状态，原样保留。
- [任务审阅说明](followups/devin-startup-guide/README.md) 已归档。其中「用户补充确认」指 Root 在任务说明中转述的新任务启动观察，并非用户另作的事实确认。

### B. 测试配置发现用例受真实 fleet 根目录影响

- 三个任务直接在 fleet worktree 跑完整测试时，`test_config_discovery_bases_and_errors` 失败：用例删除 FLEET_ROOT 并切到无配置 cwd 后，find_root 仍从脚本路径祖先找到真实 `.herdr-fleet.toml`，预期报错的 ls 返回 0。
- Root 已读取实际失败堆栈。Devin 报告在 base957b910 也能复现，但初次 base 运行的完整原始输出未保存，不将该归因当作 Root 独立复核结论。
- 各精确 HEAD 快照在没有真实 fleet 配置祖先的目录中完整通过，组合仓库同样通过。
- 已交付修复 [PR #44](https://github.com/tcuni24/tc-skills/pull/44)，HEAD `dc5d61ba6bd2d720dbda1c02b1ed28b8da363d04`，已 MERGED（合并提交 `c927ac4028c6ca0aa22bd05bb7f373022ed8f165`）。仅改测试：无配置场景把脚本副本放进无配置目录，同时隔离 cwd 与脚本祖先；新增断言保留合法的脚本祖先配置发现，确认只列出合成根目录的任务。原失败断言保留，无 skip。
- 在当前 main `468b1a8` 的真实 fleet worktree 重新保存了目标失败日志，不依赖早期未保存的 base 运行。修复后相同 worktree 完整 **133 passed / EXIT=0**，精确 HEAD 的无配置祖先快照同样 **133 passed / EXIT=0**。
- Root 已检查完整 diff、两份完整通过日志及新鲜失败日志，比对快照全部 155 个 tracked 文件与提交一致，diff --check 通过。Python 3.11 / 3.13 CI 均 SUCCESS。
- 合并后真实 fleet worktree 中的测试不再受脚本路径祖先的真实 fleet 配置影响，main 基线上的复核结果见「合并后 main 组合验收」。
- 合并元数据 `followups/config-test-isolation/pr-44-merged.json`；同目录 `pr-44.json` 的 OPEN 为合并前历史状态，原样保留。
- [复现与验收命令、完整日志](followups/config-test-isolation/commands.md) 已归档；测试采用 fake-herdr，不增加真实 Herdr 端到端验证。

### C. 已有界复现：task_event 的锁内 agent get 阻塞其他任务

原先仅确认代码模式，没有性能实验。后续诊断在 `98831bf4613b7110ff876e5e5f36148bb55d0781` 上使用隔离 state 与 fake-herdr barrier，两次均 15/15 检查通过：

- 暂停 task-a 的 `agent get` 时，`/proc/locks` 显示另一任务 event、未登记 pane 的 no-op event、只读 inbox 均在同一 fleet state 锁 inode 上等待；另一任务尚未到达自己的 `agent get`。
- 释放 barrier 后四个进程全部 exit 0，两条终态正常 pending，两个任务的轮次/去重状态正确；未操作真实 pane。
- Root 已读取完整实验脚本和两次输出，并核对诊断 worktree 的 HEAD 与干净状态。诊断代码没有修改仓库。
- 证据只证明锁序与排队关系；未测真实 Herdr IPC 卡顿概率/时长，也未实际测试其他所有命令的同窗口行为。

[诊断说明、脚本和两次完整输出](followups/event-lock-audit/README.md) 已归档。脚本指向保留的诊断 worktree，运行环境和原 SHA 见其 `commands.md`。

Root 据此将修复限定为：仅把 `agent get` 移到全局锁外，保留同任务锁，并在写入前复核 pane 的 root/slug/role 绑定；通用 IPC 超时不并入本项。

已交付 [PR #45](https://github.com/tcuni24/tc-skills/pull/45)，代码验收 HEAD `102dcad6cbe0c972640b664f6af2a8b32e422985`：

- 写阶段重新读取 TASK.md，保留轮次、去重、superseded 逻辑；`exited` 仍不调用 `agent get`。
- 独立新增 `test_event_lock.py` 的 6 项测试通过；barrier 暂停任务 A 时，其他任务 event/no-op event/inbox 均可完成，同任务 event/send 仍串行；pane 解绑或改绑时丢弃旧查询结果。
- 精确 HEAD 的隔离快照中两测试文件合计 **139 passed / exit 0**；真实 worktree 新文件 6 passed，但既有套件仍为 132 passed / 1 failed（B 尚未合并到此分支）。旧生产代码与新测试对照为 5 failed / 1 passed，语义保持用例本来就应通过。
- Root 已读取生产 diff、新测试全文和所有完整日志，比对快照全部 156 个 tracked 文件与代码验收 HEAD 一致；该 HEAD 的 Python 3.11 / 3.13 CI 均 SUCCESS。
- 绑定交错通过改写合成 panes.json 模拟；未真实运行 open/done/bind 并发，也未实测真实 IPC 延迟。不能将本测试扩称为所有交错或真实 Herdr 验证。
- A 停止写入后，同一 Devin 追加纯文档提交，将 workflow 两处旧锁说明同步为新实现。最终 HEAD `d803c9dbd8d56500266fb8ef013120a6cf2cceba`；Root 核对新增提交只改文档，生产代码与新测试相对 `102dcad` 逐字节不变，diff --check 通过。最终 HEAD 的 Python 3.11 / 3.13 CI 均 SUCCESS；已 MERGED（合并提交 `12783ac14b4e23ce65dd5ac890acee4b894097b2`）。
- 合并元数据 `followups/event-query-lock/pr-45-merged.json`；同目录 `pr-45-code.json`、`pr-45-final.json` 的 OPEN 为合并前历史状态，原样保留。
- Root 静态 `merge-tree --write-tree` 核对 PR #43 与 #45 的文档改动可无冲突合入；这不是三项后续 PR 的组合运行测试。

[修复验收命令和完整日志](followups/event-query-lock/commands.md) 已归档；代码阶段 CI 元数据见 `pr-45-code.json`，最终 HEAD 的双版本 CI 见 `pr-45-final.json`。

## 后续交付汇总

| 记录 | PR | 最终 HEAD | 验收 | 状态 |
| --- | --- | --- | --- | --- |
| A 启动与投递恢复 | [#43](https://github.com/tcuni24/tc-skills/pull/43) | `7c180c4535d4f524e3516016c9ed17431e63ce66` | 纯文档复核、双版本 CI SUCCESS | MERGED `16f3ac78`；检测缺口未修，提供操作指引 |
| B 测试隔离 | [#44](https://github.com/tcuni24/tc-skills/pull/44) | `dc5d61ba6bd2d720dbda1c02b1ed28b8da363d04` | worktree/快照各 133 passed、双版本 CI SUCCESS | MERGED `c927ac40` |
| C 事件查询锁 | [#45](https://github.com/tcuni24/tc-skills/pull/45) | `d803c9dbd8d56500266fb8ef013120a6cf2cceba` | 代码阶段快照 139 passed；最终仅改文档，双版本 CI SUCCESS | MERGED `12783ac1`（当前 origin/main） |

## 合并后 main 组合验收

本轮归档任务在合并后的 main 基线上对两测试文件做了一次完整组合运行：

- tested base：`12783ac14b4e23ce65dd5ac890acee4b894097b2`（origin/main = #45 合并提交）
- 执行者：归档任务 agent（Devin），在真实 fleet worktree `/home/glx/scripts/tc-skills-fleet/worktrees/fleet-closeout-report/tc-skills`（祖先含真实 `.herdr-fleet.toml`）中运行；非 Root 亲自执行。
- 命令：`/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python -m pytest tests/herdr-fleet/test_fleet.py tests/herdr-fleet/test_event_lock.py -q`
- 结果：**139 passed in 157.78s，EXIT=0**。B 修复已生效，未再出现祖先配置导致的 `test_config_discovery_bases_and_errors` 失败。
- 边界：fake-herdr 测试，非真实 Herdr 端到端；仅上述两文件，未跑其他套件；未实测真实 IPC 时长。

归档：[命令与环境](merged/final-main/commands.md)、[完整原始日志](merged/final-main/pytest.log)、[退出码](merged/final-main/exit-code.txt)、[tested base](merged/final-main/tested-base.txt)。

## 收尾状态

- 六个 PR（#40/#41/#42 与后续 #43/#44/#45）均已 MERGED；原三个合并前已更新至各自最新 main，后续三个以验收最终 HEAD 直接合入，全部 CI SUCCESS。main 功能验收基线为 `12783ac`。
- 历史组合补丁与各轮历史验收材料保留原样；`pr-*-final.json` 及 followups 内早期 JSON 中的 OPEN 均为合并前历史状态。
- A 已随 #43 落地为启动/恢复操作指引，上游 readiness 就绪检测缺口仍未修；B 已随 #44 落地，真实 fleet worktree 测试不再受脚本祖先配置影响；C 已随 #45 落地，fake-herdr/模拟重绑边界不变，真实 IPC 时长未测。
- 未清理 worktree 或删除分支；此前现场证据保持原样。未把旧 `2026-10-30-devin-perf-smoke/` 现场目录加入任何 PR。
- 本报告及全部归档经本归档 PR 提交；生产代码与测试无任何改动。

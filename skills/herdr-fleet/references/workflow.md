# herdr-fleet 工作流参考

一个功能或 bug 对应一个任务。每个任务包含：

- 一组 git worktree：`<项目根>/worktrees/<slug>/<dir>`，每个仓库一个，分支都是 `<type>/<slug>`（`type` 默认 `feat`，`--fix` 时为 `fix`）
- 一个 herdr tab：tab 名就是 slug，左侧是任务 agent，右侧是 shell，工作目录都是任务目录
- `TASK.md`：记录各仓库的分支、base、base commit、agent 种类与参数，以及需求原文

任务 agent 的工作目录在项目根之内，所以能读到项目根和各仓库 worktree 里的 AGENTS.md。

## 角色

- **主 agent**（控制 tab，加载本 skill）：拆分需求，`fleet new` 开任务，`fleet ls/peek` 查看情况，`fleet send` 转达指令，`fleet done` 收尾。不写代码。
- **任务 agent**（任务 tab）：在 `worktrees/<slug>` 中按项目约定实现、测试、提 PR。

## 配置

项目根放一个 `.herdr-fleet.toml`。`fleet` 从当前目录向上找它；也可以用环境变量 `FLEET_ROOT` 指定项目根。

```toml
# 任务 agent 的默认种类（herdr agent start --kind）；none 表示只建 tab 不启动 agent
agent = "pi"
# 可选：项目根本身是 git 仓库且没有 origin/HEAD 时，gc 用它判断根仓库 worktree 是否已合并
root_base = "origin/master"

[agents.devin]                   # agent 种类只能以小写字母开头，含小写字母、数字和连字符
args = ["--model", "example"]  # 可选，须为字符串列表

[repos.web]                      # 键用于 -r 和 --base，只能是小写字母和数字
path = "project-deliver-web"     # 主 checkout，相对项目根
dir = "web"                      # 任务目录里的子目录名，默认等于键
# base = "origin/develop"        # 可选，默认取该仓库的 origin/HEAD
shared = ["node_modules"]        # 可选：从主 checkout 软链进 worktree 的条目（须被该仓库 ignore）
lockfile = "pnpm-lock.yaml"      # 可选：worktree 与主 checkout 的该文件不同时不建软链
install = "pnpm i"               # 可选：不建软链时提示任务 agent 执行的安装命令

[repos.be]
path = "deliver-backend"
dir = "backend"
```

- 项目根是 git 仓库时，应在其 `.gitignore` 里忽略 `worktrees/`；否则 `fleet new` 会警告。
- 不建软链的原因有两种：lockfile 不一致，或主 checkout 里没有该条目。这时会把原因和 `install` 命令写进 `TASK.md`，并告诉任务 agent 在自己的 worktree 里安装。不一致时还软链，任务 agent 一装依赖就会改到主 checkout。

## 命令

```sh
fleet new my-task --agent devin --agent-arg=--model --agent-arg example --prompt '需求描述' # --agent none 不启动 agent；--agent-arg 可重复
fleet ls                                 # 任务、tab、agent 状态、dirty、ahead/behind、合并状态；--json；--fetch 先 fetch
fleet status my-task [--lines N] [--json] # 提交、diff 统计、未提交文件、PR、agent 最近输出；默认 12 行，0 不显示，负数报错
fleet wait my-task [other-task ...] [--any|--all] [--timeout SEC] # 等待本轮终态
fleet peek my-task [--lines N]           # 任务 agent 的最近输出
fleet send my-task '补充要求'
fleet open my-task [--agent devin] [--agent-arg=--flag] # tab 丢失后重建；已有 tab 时只切过去
fleet inbox [--all] [--json]             # 待投递事件；--all 包含已投递历史
fleet bind                               # 将当前项目的任务改绑到当前 pane
fleet event --pane PANE --status STATUS  # 处理状态边沿（供插件钩子调用）
fleet done my-task [--dry-run]           # 预检并预览，或删除 worktree、已合并分支和 tab
fleet gc [--apply]                       # 报告所有仓库的全部 worktree（包括不在 worktrees/<slug>/ 下的旧位置），默认只读
```

`new` 还支持 `-r web`、`--fix`、`--base web=origin/x` 和 `--no-fetch`。`--agent-arg` 按顺序传递，以 `-` 开头的值须用 `--agent-arg=--flag`。命令行参数只要出现一次，就整体替换所选种类的配置 `args`；未配置时不附加参数。参数以 argv 传递，不经 shell。

`new` 把实际选择的种类和参数写进 `TASK.md`；`open` 重建时默认复用记录，显式 `--agent-arg` 整体替换参数，显式 `--agent` 切换种类时使用新种类的配置参数。成功重建后更新 agent 记录，已有 tab 只切换、不更改 agent 记录。旧任务没有记录时回退到配置默认。`--agent none` 不启动 agent，记录的参数为空。`TASK.md` 明文保存参数，不要在参数中放密钥。

`peek` 和 `send` 按 tab 找到任务 agent 所在的 pane，再对 pane 操作。`herdr agent list` 里的 `agent` 字段是种类（如 `pi`），多个任务同时用 pi 时不唯一。

`new` 和 `open` 在设置了 `HERDR_PANE_ID` 时将该主 agent pane 记为 `coordinator_pane`。`bind` 将当前项目所有任务改绑到当前 pane，待投递事件也改绑；已投递与被取代的历史保留原目标。旧任务没有此字段时仍可使用。pane 索引和收件箱位于 `$XDG_STATE_HOME/herdr-fleet/`（默认 `~/.local/state/herdr-fleet/`），不占用任务目录；读写通过文件锁协调，`done` 清除对应记录，`--dry-run` 不清除。收件箱记录项目根、任务、目标 pane、终态、时间、去重键、事件轮次基线（`baseline`）、投递状态、尝试次数和最近错误；`delivery` 为 `pending`/`delivered`/`superseded`。`inbox` 不改变状态，默认只列 `pending`，`--all` 含已投递与被取代的记录。

`event` 接受 `working`、`idle`、`done`、`blocked`、`exited`。任务每次成功提交以 `submit_seq` 为轮次基线；working 边沿也可开始手动输入的下一轮。只记录晚于基线且尚未通知的终态，重复和过期边沿不重复入箱。每条事件保存产生它的轮次基线（与去重键第三项同值；字段加入前的旧记录按去重键解析，无法解析则轮次未知）。主 agent 为 idle/done 时尝试投递，否则保留待投递；主 agent 的 idle/done 边沿会合并投递待投递事件。投递须得到 `agent_prompted` 确认，失败时保留记录和错误；pane 不存在时显示一次 herdr 通知。通知只是触发器，须用 `fleet status` 核查。插件将 `pane.exited`、`pane.closed` 与 `tab.closed` 映射为任务 `exited` 事件；关 tab 时通过索引的 tab_id 查找任务 pane。旧索引可通过 `fleet bind` 或重新打开任务补登记。仅安装本命令不会自动订阅事件。

新轮次建立后（`new`/`send` 提交确认，或开启下一轮的手动 working 边沿），同一任务更早轮次里仍待投递的 done/idle 事件被标记 `superseded`：记录保留在 `--all` 中、尝试次数不变、默认列表不再出现，也绝不会再投递给主 agent。提交失败不建立轮次，也就不取代任何事件。其他任务的事件、当前新轮次的事件不受影响；blocked 与 exited 不自动取代——blocked 承载用户决策，仍按下面的轮次标注投递。

投递时消息在锁外构造（git/gh 等昂贵调用不在锁内重复），随后在投递锁与所涉任务锁（按 slug 排序，先取投递锁再取任务锁）内复核：待投递集合未变、构造所用的各任务当前轮次快照未漂移（漂移则放回锁外重新构造，绝不投递旧快照文本），且各 done/idle 事件的轮次基线不早于该任务 `TASK.md` 当前 `event_round.baseline`；复核不过的 done/idle 改标 `superseded` 并重建消息，绝不投递。复核与 `agent prompt` 在同一组任务锁内完成，使 `send`/轮次边沿无法插入二者之间。此投递路径中跨任务全局状态锁只在读写状态与任务本地文件（panes/inbox、`TASK.md`）时持有，调用 herdr/git 期间不持有；`task_event` 的 `agent get` 同样只在任务锁内、全局状态锁外执行。

通知每行格式为 `<slug> <终态> [round <事件轮次>] <phase> <PR>`。事件轮次已被取代但仍需投递的事件（blocked、exited）显示 `[round <事件轮次> → <当前轮次>] 当前状态 <实时 agent 状态>`，phase 按实时状态而非旧终态计算；轮次无法确定时显示 `[round ?]`。

开始确认只阻塞同一任务的事件处理，不持有跨任务的状态锁。事件处理（`task_event`）在调用方持有的 `task_lock` 内、`fleet_state` 外执行 `agent get`，之后重入 `fleet_state(write=True)` 复核 `panes` 中该 pane 仍绑定同一 `root`/`slug`/`role`，再按重读的 `TASK.md` 与收件箱处理轮次、去重与取代；pane 在查询期间被重绑到其他任务或移除时丢弃该次结果，`exited` 不发起 `agent get`。herdr 状态钩子不带序号：处理 `working` 时若实时状态已是终态，则以实时序号推断快速结束的手动轮次；已有通知时实时序号必须晚于上次终态。单独的 `done` → `idle` 后续边沿不会再次通知。但若上一轮迟到的 `working` 恰好在同轮 `done` → `idle` 后才处理，它与快速完成的新轮次无法区分，可能额外通知一次；此取舍避免静默漏掉新轮次。

手动新轮次的 `working` 钩子尚未处理时，若 agent 已退出且 `exited` 先到，而上一轮已通知或没有轮次记录，则该轮不会通知。钩子载荷没有序号，无法把它与同一轮 `done` 后退出区分，不能简单地对每次退出都发通知。

投递先构造消息，再在锁内复核构造所用的轮次快照、待投递集合与 done/idle 基线，然后查询主 agent 状态、发送；轮次复核与 prompt 之间无窗口（同一组任务锁）。剩余窗口：主 agent 状态查询和 `agent prompt` 之间——herdr 没有「仅空闲时接受」的原子条件，主 agent 可能在检查后重新开始工作；通知行中 `当前状态` 取自构造时刻的实时查询，与投递时刻可能有秒级偏差。herdr 在提交时 agent 已为 `blocked` 会以 `agent_blocked` 拒绝，处于 `working` 时 prompt 是排队还是打断尚未真机验证。

钩子载荷没有序号：事件上的轮次基线是入箱时按当时 TASK.md 记下的值（与去重键第三项相同），不是事后从当前 TASK.md 反推的真实历史；字段加入前的旧记录按去重键还原同一记录值，去重键也无法解析时只能按轮次未知处理。

仅在真机 herdr 0.9.1 上实测，`state_change_seq` 跨 pane 全局递增（多个 pane 间从 9185 到 9238），新 pane 不复用旧序号；因此该版本中 `open` 重建后保留旧 `submit_seq`/`event_round` 不会因序号复用而误去重。

`new` 要求任务 agent 在最终回复中输出回报块，并放进 `` ```text `` fenced code block：

```text
FLEET-REPORT
结果：完成 / 部分完成 / 阻塞
PR：链接或无
测试：每条命令及退出码
证据边界：哪些已真实验证，哪些只是合成数据、mock、空页面或未覆盖
需要决策：需要用户决定的事项或无
END FLEET-REPORT
```

普通 Markdown 段落会把单换行折叠成一段（字段和标记挤在同一逻辑行），fenced code block 才能保留逐行字段；解析器按原样摘取文本，不还原被折叠的换行语义。

`status` 从所读取的最近输出中摘取最后一个完整回报块：开始标记是行首的 `FLEET-REPORT`（允许前导空白），结束标记是 `END FLEET-REPORT` 子串（可跟在字段内容同一行）。标记不得与字母、数字、`-`、`_` 直接相邻，因此 `FLEET-REPORT-EXAMPLE`、`APPEND FLEET-REPORT` 之类不算标记。摘取结果做 dedent：去掉各行公共前导空白，字段自身的缩进和已有换行保留。文本显示在「回报」一节，JSON 的 `report` 为原文或 `null`。只有开头没有结尾不算完整块，末尾未闭合的块不会盖掉前一个完整块；输出行数不足可能截断回报，可增大 `--lines`。`--lines 0` 不读取 agent 输出，也不摘取回报块。`send` 的补充说明不改变回报要求。

herdr 可执行文件依次取 `FLEET_HERDR`、`HERDR_BIN_PATH`，都没有时用 `herdr`。
PR 查询用 `FLEET_GH` 指定可执行文件，默认 `gh`。fleet 以 `pr list --head <分支> --state all --json number,url,state --limit 1` 调用它（工作目录为该仓库主 checkout），期望输出与 `gh pr list` 相同的 JSON 数组：空数组表示没有 PR；每项须是含 `number`（正整数）、`url`（非空字符串）、`state`（非空字符串，不限枚举）的对象；查询失败要以非零退出码结束并在 stderr 写明原因。对接其他代码托管时，`FLEET_GH` 可指向满足同一参数与输出契约的适配程序；该适配程序的失败同样只产生 unknown，不影响任务信息展示。
查询结果分三态：`found`（查到 PR）、`none`（查询成功且返回空数组）、`unknown`（非零退出、无法执行、超时、非法 JSON 或结构不符）。`status` 文本依次显示 `PR: #12 …`、`PR: 无`、`PR: 未知（查询失败：…）`；`status --json` 的 `repos.<key>` 新增 `pr_state` 和 `pr_error`，`pr` 在 found 时为原 PR 对象、其余为 null。任一仓库为 unknown 时 `next_step` 会要求先人工核查 PR 是否已存在，不再无条件建议提 PR；自动通知里 unknown 以 `?` 标记（与已查到的链接并存时追加 `?`），`-` 只表示各仓库都确认无 PR。PR 查询失败不阻塞收尾。`ls --json` 保留原有键，新增 `agent_kind` 和 `phase`，出错时新增 `error`。
`ls` 表格包含 tab 列，tab 存在但没有 agent 时可与没有 tab 区分。`status` 的文本和 JSON 在仓库异常时显示 `phase=error` 和原因，尽力保留其他仓库的信息，返回码为 0（与 `ls` 一致）。

phase 按以下顺序判断（仓库异常时为 `error`）：

| phase | 含义 |
| --- | --- |
| error | 仓库信息读取失败 |
| no-tab | 没有 tab 或 agent |
| working | agent 正在工作 |
| blocked | agent 等待决策 |
| unknown | agent 状态未知，或仓库状态组合无法归类 |
| merged | 所有仓库 merged 或 squash-merged |
| uncommitted | 任一仓库有未提交文件 |
| review | 任一仓库有领先提交且 unmerged |
| idle-empty | 所有仓库 no-commits |

`wait` 默认等待全部任务，`--any` 等首个终态；只认最近一次提交之后的终态。返回码：idle/done 为 0，blocked 为 3，超时为 4，无 agent 或其他错误为 1。观察到终态后，仓库信息读取失败仅报告错误，不改变终态返回码。开任务后运行 `fleet doctor`：通过时可以结束本轮并告知用户会自动通知；不通过时不得声称会自动通知，必须在同一轮内 `fleet wait` 到任务结束或阻塞，不能只说明不会自动通知就结束本轮。`done --dry-run` 预检通过返回 0，拒绝返回 1，且不删除或关闭 tab。

## 任务 agent 启动与提交恢复

以下行为只在 herdr 0.9.1、devin 集成 v2（Devin CLI v3000.11.234）上实测确认；其他版本或其他 agent 种类未验证，只能当排查线索，不能当既定事实。

### Devin 新目录的信任选择页

devin 任务 agent 在未曾信任过的目录（如 `fleet new` 刚建出的 `worktrees/<slug>/`）启动时，可能停在目录信任选择页，而不是任务输入页：

```text
✱ Do you trust the authors of this directory?
 ~/…/worktrees/<slug>

 ❭ 1 Yes, trust
   2 No, exit

 ↑↓ select · ↵ confirm · esc cancel
```

实测此页停留期间 `herdr agent get <pane>`（输出为 JSON）仍可能显示 `agent_status=idle`、`interactive_ready=true`，而 `agent_session` 字段缺失：即该版本上信任选择页会被误报为就绪。同一版本上另记录过 `fleet new --prompt` 和 `fleet send` 开始确认超时（`开始确认失败：…timeout…`），失败后 worktree 和 tab 按「安全规则」保留；但超时的完整输入处理链未捕获，不能断定超时当刻正处于信任选择页，也不能断定 prompt 被其消耗。因此 `idle`、`interactive_ready`、`agent_session` 任何单项都不足以证明 agent 已到正式输入页，判定以 `fleet peek`（`herdr agent read`）看到的实际屏幕为准。

任务 agent 为 devin 时，稳妥的开任务顺序：

1. `fleet new <slug> …` 不带 `--prompt`，先让 agent 在新目录里起好。
2. `fleet peek <slug>` 配合 `herdr agent get <pane>` 看实际屏幕：
   - 显示目录信任选择页：先核对页面上的目录确为本任务刚建的 `worktrees/<slug>/`（即用户授权的任务目录），不匹配或来历不明时绝不答 "Yes, trust"；核对无误后按页面提示作答该选择（`herdr agent send-keys <pane> <键>`，按键以页面选项提示为准；按键无法确定时停下，由用户在任务 tab 中人工确认）。
   - 已是正式输入页：直接进入下一步。
   - 其他未知页面：停止，`peek`/`agent get` 留证并向用户报告，不要猜测性发送按键或 prompt。
3. 再次 `herdr agent get` 确认 `agent_session` 已出现，且 `fleet peek` 显示正式输入页后，用 `fleet send <slug> '<任务说明>'` 提交。

`fleet send` 不自动附带 `new --prompt` 的 worktree 上下文与 FLEET-REPORT 回报块要求；用它补交任务说明时，主 agent 须把任务目录、各仓库分支与 base、"不要改主 checkout"和回报块格式写进发送文本。该选择页是 devin 集成的行为；其他 agent 种类没有相同证据，不要给它们套用额外的启动门槛或等待。`fleet doctor` 只验证环境（插件、coordinator_pane、待投递事件），通过不代表 prompt 已被执行。

### 开始确认失败后的投递不确定

`fleet new --prompt` 或 `fleet send` 报 `开始确认失败`（如 `timeout`、`agent_prompt_stalled`）时，提交结果不确定：开始确认只等启动信号，不追踪整个回合，prompt 可能没送到，也可能已送达或排队而尚未开始执行。失败后 worktree、tab 和 `TASK.md` 的 `submit_seq`（提交时记录的序号）都保留，供检查恢复。未核实前不得重发：重复提交会让同一任务执行两遍。

以下检查只帮助排查，不能自动推出"未投递"：

1. `herdr agent get <pane>`：对比 `state_change_seq` 与 `TASK.md` 的 `submit_seq`；seq 已推进且状态为 idle/done/blocked，说明提交后发生过状态变化。
2. `fleet peek <slug>`：终端是只有启动页/选择页、无任何任务执行输出，还是已出现对任务的处理痕迹。
3. 看任务目录与各 worktree：`git status`、`git log` 和新增文件是否显示任务已开始执行。

即使 seq 未推进、终端无执行痕迹、无任务产物三项同时成立，也只说明未观察到执行开始——prompt 仍可能已被接收或排队，不构成"未投递"的确认。因此开始确认超时且没有明确的未送达证据时，持续把该提交标记为"投递不确定"并向用户报告已核实的事实，不得重发；只有取得"原请求未被接受或不会再执行"的明确证据后才恢复 `fleet send`。本轮"核对目录、处理信任选择页、再 send 成功"是已验证的一次成功恢复，不能据此外推成"缺失证据即未投递"的通用判定。

本节是操作规避：绕开"信任选择页被判为就绪"的检测缺口，不代表 herdr 或 devin 的就绪检测已修复。超时 prompt 的去向不要臆测：未被证实被信任页消耗，也不要归因为额度。

## 任务收尾

PR 合并只说明成果已交付，协调者的收尾对象还有任务 tab、各仓库 worktree、本地分支、`TASK.md` 和 pane/收件箱状态记录。[SKILL.md 第 4 节](../SKILL.md#4-收尾)给出入口与授权边界：已授权的合并及常规收尾沿用会话授权，本节不构成对未授权合并、放弃、`--discard`、解锁或 `gc --apply` 的授权。

### 核查顺序

在 `fleet ls` 显示 merged（或用户确认放弃）且任务 agent 已停写之后。停写须有成功观察为据：`wait` 观察到终态返回（idle/done 为 0、blocked 为 3），或 `status`/`ls`/`peek` 显示 idle/done/已退出且无仍在产生的输出；`wait` 超时（4）与出错（1）不能充当停写证据，`exited` 不经 `wait` 判定，由通知、`ls`、`peek` 观察：

1. 逐仓库真实核查：`fleet status <slug>` 看提交、diff 统计、未提交文件与 PR 三态，对照 `fleet ls` 的 phase；不凭通知或记忆下结论。
2. 依赖核查：本 worktree 的内容、共享 venv、软链是否还被其他任务或进程引用。`done` 只摘除本 worktree 里 `shared` 配置项的出向软链（指向主 checkout），不会像 `gc --apply` 那样改指其他 worktree 指进来的入向依赖；`fleet gc` 只读报告的 `dependents` 列即各 worktree 的入向消费者，再 `readlink` 核实指向。有消费者时先按「任务目录含额外文件」一节解决依赖或写明保留条件，不能先 `done` 事后才发现。
3. `fleet done <slug> --dry-run` 预览：逐仓库看是"将删除"还是"被拒绝"。
4. `fleet done <slug>` 执行：先摘除 worktree 内指向主 checkout 的共享软链，再删 worktree；merged/no-commits 删本地分支，unmerged 保留分支（需 `--force`，属放弃授权）；删 `TASK.md`、任务目录和 pane/收件箱记录；关闭存在的 tab。`done` 只动本地分支，不删远端分支。
5. 收尾后再核查：`fleet ls` 中该任务已消失；各主 checkout 里 `git worktree list` 不再列出这些 worktree、`git branch` 不再有已删分支；tab 已关闭；`git status` 确认主 checkout 未被改动；其他任务的 worktree 与分支不受影响。

几个边界：

- PR 仍 OPEN（待 review/merge）时不为通过清理强行删 worktree：等合并，或按已授权的放弃流程处理。
- `no-commits`/`idle-empty` 常见于诊断、答疑类任务，只说明相对 base 没有新提交，既不等于"已合并"也不等于"无需收尾"：先 `status`/`peek`/`ls` 确认没有仍需保留的工作（未推送提交、未提的 PR、留在 worktree 里的结论文件），再照常 dry-run → done；要保留的内容先按下节移出。
- `done` 只处理本任务声明的 worktree 与状态。`gc --apply` 只删干净且已合并的 worktree（删前把其他 worktree 指向它的共享软链改指主 checkout 同名条目），不管 tab、`TASK.md`、分支和状态记录：任务收尾走 `done`，`gc` 留给 `done` 之外的遗留，且按既有授权边界先出报告。

### 任务目录含额外文件：先备份再移出

`done` 预检要求任务目录恰为 `TASK.md` 加各仓库子目录；多出的文件或目录（产物、日志、依赖目录，含点文件）直接拒绝。这一拒绝与 dirty/unmerged/locked 相互独立，不自动等于"可丢弃"；不得为此加 `--force`/`--discard` 或 `rm -rf`/`git clean` 强推。fleet 没有自动备份能力，以下由协调者手动执行：

1. **清点**：列出额外条目，确认各自由谁产生、还有谁在用。特别注意符号链接与共享依赖：配置 `shared` 的目录（如 `node_modules`、venv）可能正被其他任务的 worktree 软链引用，整体搬走会弄断它们。用 `readlink`/`ls -l` 核对每个链接指向哪里、还有谁指着它。
2. **备份**：把需保全的材料复制到唯一备份路径——须在本次全部待清理任务目录以外，不能落在另一个待清理任务目录下。至少含 `TASK.md`、各 worktree 的 `git branch --show-current` 与 `git rev-parse HEAD`（对照 TASK.md 的 branch/base/base_commit）、关键命令与退出码、原始日志和证据文件。
3. **核对备份**：普通文件与符号链接分开核对——普通文件按 `sha256sum`（或逐文件 `cmp`）确认字节一致；符号链接只比对 `readlink` 目标字符串，不用会跟随链接的比法（`diff -r` 会跟随软链，不作依据）。备份失败或核对不一致时不删源、不继续 `done`。
4. **移出**：确认依赖已解决（没有其他任务或进程仍在引用）后，把额外条目 `mv` 到任务目录以外、仍可找回的位置，保持可逆；仍被引用的共享目录先解决依赖再动。
5. **重跑预检**：`done --dry-run` 通过后再 `done`。备份完成不是收尾终点，`done` 成功且事后核查通过才算收尾。

### 收尾汇报

向用户的最终汇报必须写清：清理是否完成（`done` 真正执行且事后核查通过，而不是只有"PR 已合并"）；证据与备份的绝对路径；每项保留例外的具体原因与再处理条件（如"PR #N 待 review，合并后重跑 `done`""`<路径>` 仍被 `<任务>` 的软链引用，待其收尾后处理"）。只写"worktree 保留"不构成汇报；默认可执行的常规收尾应继续做，确有阻塞才报未完成，未合并的 PR 不得声称已全部收尾。

## 安全规则

- `new` 创建 worktree、tab、agent 或 pane 失败时，回滚已建的 worktree、新建的分支和 tab；提交 prompt 后开始确认失败则保留 worktree 和 tab，供检查和恢复。
- `done` 先对所有仓库预检，任何一项不过就什么都不删：
  - 任务目录里有 `TASK.md` 和各仓库子目录以外的东西：拒绝。
  - worktree 当前分支和 `TASK.md` 记录不符：拒绝。
  - worktree 已 locked：拒绝，不自动解锁，`--force` 和 `--discard` 也不能覆盖。
  - 有未提交内容：需要 `--discard`。
  - 未合并：需要 `--force`，分支会保留。
  - 已经 merge 或 squash 合并（包括远端源分支已删除的情况）可以直接清理，并删除本地分支。
- 合并状态：`no-commits`（相对创建时的 base 没有新提交）、`merged`、`squash-merged`、`unmerged`。squash 判断会生成一个临时提交，写在临时对象目录里，不会写入仓库。
- `gc` 默认只出报告。`gc --apply` 只删除干净且已合并的 worktree。删除前，其他 worktree 里指向它的共享软链会改指到主 checkout 的同名条目；主 checkout 缺这个条目时跳过，不删除。
- 旧位置（如 `<仓库>/worktrees/<name>`）的 worktree 由 `gc` 识别为 `legacy`，不会自动迁移。

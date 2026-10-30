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

`new` 和 `open` 在设置了 `HERDR_PANE_ID` 时将该主 agent pane 记为 `coordinator_pane`。`bind` 将当前项目所有任务改绑到当前 pane，待投递事件也改绑；已投递历史保留原目标。旧任务没有此字段时仍可使用。pane 索引和收件箱位于 `$XDG_STATE_HOME/herdr-fleet/`（默认 `~/.local/state/herdr-fleet/`），不占用任务目录；读写通过文件锁协调，`done` 清除对应记录，`--dry-run` 不清除。收件箱记录项目根、任务、目标 pane、终态、时间、去重键、投递状态、尝试次数和最近错误。`inbox` 不改变状态。

`event` 接受 `working`、`idle`、`done`、`blocked`、`exited`。任务每次成功提交以 `submit_seq` 为轮次基线；working 边沿也可开始手动输入的下一轮。只记录晚于基线且尚未通知的终态，重复和过期边沿不重复入箱。主 agent 为 idle/done 时尝试投递，否则保留待投递；主 agent 的 idle/done 边沿会合并投递待投递事件。投递须得到 `agent_prompted` 确认，失败时保留记录和错误；pane 不存在时显示一次 herdr 通知。通知只是触发器，须用 `fleet status` 核查。`event` 由后续插件调用；仅安装本命令不会自动订阅事件。

`new` 要求任务 agent 在最终回复中输出回报块：

```text
FLEET-REPORT
结果：完成 / 部分完成 / 阻塞
PR：链接或无
测试：每条命令及退出码
证据边界：哪些已真实验证，哪些只是合成数据、mock、空页面或未覆盖
需要决策：需要用户决定的事项或无
END FLEET-REPORT
```

`status` 从所读取的最近输出中摘取最后一个完整回报块，文本显示在「回报」一节，JSON 的 `report` 为原文或 `null`。只有开头没有结尾不算完整块；输出行数不足可能截断回报，可增大 `--lines`。`--lines 0` 不读取 agent 输出，也不摘取回报块。`send` 的补充说明不改变回报要求。

herdr 可执行文件依次取 `FLEET_HERDR`、`HERDR_BIN_PATH`，都没有时用 `herdr`。
PR 查询用 `FLEET_GH` 指定可执行文件，默认 `gh`；失败或超时显示未知，不阻塞收尾。`ls --json` 保留原有键，新增 `agent_kind` 和 `phase`，出错时新增 `error`。
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

`wait` 默认等待全部任务，`--any` 等首个终态；只认最近一次提交之后的终态。返回码：idle/done 为 0，blocked 为 3，超时为 4，无 agent 或其他错误为 1。观察到终态后，仓库信息读取失败仅报告错误，不改变终态返回码。当前没有自动通知，主 agent 结束本轮前必须 `fleet wait` 到任务结束或阻塞，不得声称完成后会自动通知。`done --dry-run` 预检通过返回 0，拒绝返回 1，且不删除或关闭 tab。

## 安全规则

- `new` 中途任何一步失败（worktree、tab、agent、pane），都会回滚已建的 worktree、新建的分支和 tab。
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

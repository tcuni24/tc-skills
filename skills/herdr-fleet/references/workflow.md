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
fleet ls                                 # 任务、tab、agent、状态、阶段、仓库；--json；--fetch 先 fetch
fleet status my-task [--lines N] [--json] # 提交、diff 统计、未提交文件、PR、agent 最近输出；默认 12 行，0 不显示，负数报错
fleet wait my-task [--timeout SEC]       # 等待 idle/done/blocked
fleet peek my-task [--lines N]           # 任务 agent 的最近输出
fleet send my-task '补充要求'
fleet open my-task [--agent devin] [--agent-arg=--flag] # tab 丢失后重建；已有 tab 时只切过去
fleet done my-task [--dry-run]           # 预检并预览，或删除 worktree、已合并分支和 tab
fleet gc [--apply]                       # 报告所有仓库的全部 worktree（包括不在 worktrees/<slug>/ 下的旧位置），默认只读
```

`new` 还支持 `-r web`、`--fix`、`--base web=origin/x` 和 `--no-fetch`。`--agent-arg` 按顺序传递，以 `-` 开头的值须用 `--agent-arg=--flag`。命令行参数只要出现一次，就整体替换所选种类的配置 `args`；未配置时不附加参数。参数以 argv 传递，不经 shell。

`new` 把实际选择的种类和参数写进 `TASK.md`；`open` 重建时默认复用记录，显式 `--agent-arg` 整体替换参数，显式 `--agent` 切换种类时使用新种类的配置参数。成功重建后更新记录，已有 tab 只切换、不更改记录。旧任务没有记录时回退到配置默认。`--agent none` 不启动 agent，记录的参数为空。`TASK.md` 明文保存参数，不要在参数中放密钥。

`peek` 和 `send` 按 tab 找到任务 agent 所在的 pane，再对 pane 操作。`herdr agent list` 里的 `agent` 字段是种类（如 `pi`），多个任务同时用 pi 时不唯一。

herdr 可执行文件依次取 `FLEET_HERDR`、`HERDR_BIN_PATH`，都没有时用 `herdr`。
PR 查询用 `FLEET_GH` 指定可执行文件，默认 `gh`；失败或超时显示未知，不阻塞收尾。`ls --json` 保留原有键，新增 `agent_kind` 和 `phase`，出错时新增 `error`。
`ls` 文本表格列为任务、tab、agent（种类）、状态、阶段（phase 英文原值）、仓库；tab 存在但没有 agent 时可与没有 tab 区分。仓库按 `web 领先1 落后2 未提交3 未合并` 显示，仅在数量大于零时显示领先、落后和未提交；合并状态显示为未合并、已合并、已合并(squash)、无提交或未知。多个仓库以 `；` 分隔，仓库错误显示原错误文本；无任务时显示 `没有任务`，`--json` 仍输出 `[]`。`status` 的文本和 JSON 在仓库异常时显示 `phase=error` 和原因，尽力保留其他仓库的信息，返回码为 0（与 `ls` 一致）。

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

`wait` 返回码：idle/done 为 0，blocked 为 3，超时为 4，无 agent 或其他错误为 1。观察到终态后，仓库信息读取失败仅报告错误，不改变终态返回码。已经 idle 时立即返回；`new` 后先用 `status` 确认已 working 再等待。`done --dry-run` 预检通过返回 0，拒绝返回 1，且不删除或关闭 tab。

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

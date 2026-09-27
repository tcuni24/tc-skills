---
date: 2026-09-27
revised: 2026-09-27
scope: 全仓库（6 个 skill + docs + 仓库工程化）
baseline: 81881c7
test-baseline: 183 passed, 69 subtests passed in 69.61s（`python3 -m pytest -q`，全绿；复核重跑 69.95s）
---

# tc-skills 仓库体检报告（2026-09-27）

结论：内容质量与测试密度都不差（99 个受控文件里 9 个测试文件、共 5815 行，测试基线全绿），**问题集中在工程化外壳**——公开仓库没有任何许可证文件、没有 CI、依赖与 Python 下限未声明，以及 `herdr-pair` 的轮次记录随 skill 包一起分发。仓库卫生问题比初稿判断的轻：被忽略的本地残留不会进入分发包。下面每条都给了可复核的证据与命令。

## 结论摘要

| 优先级 | 问题 | 影响 |
| --- | --- | --- |
| P0 | 公开仓库无 `LICENSE`：README 声称 MIT 并链接不存在的文件，GitHub `licenseInfo` 为空；nextflow skill 声明 Apache-2.0 但无来源署名 | 对外仓库在法律上没有给出任何授权 |
| P1 | 没有任何 CI（`.github/` 不存在） | 现有 183 个用例全靠人工执行，改动随时可能静默破坏已覆盖行为 |
| P1 | 第三方依赖与 Python 版本下限未声明 | 干净环境 `pytest` 在收集阶段失败；也是落地 CI 的前置条件 |
| P1 | `create_gitee_pr.py` 的外部调用无超时 / 无禁交互兜底 | agent 非交互调用时，凭据提示或网络僵死会让进程一直挂住 |
| P1 | `skills/herdr-pair/reports/` 受版本控制，随 skill 包分发 | 约 180K 配对轮次记录被装到用户机器上 |
| P2 | herdr 可执行文件解析逻辑复制三份 | 已经漂移并产生过一次修复提交 |
| P2 | 目录命名、frontmatter 语言、exec 位、`agents/openai.yaml` 覆盖不一致 | skill 之间约定不统一，触发召回不稳定 |
| P2 | 单条测试 10.36 秒空等 | 占全套耗时约 15%，CI 反馈变慢 |
| P3 | `analysis/` 过程产物留在仓库根、`.gitignore` 未含 `.nfs*` | 仓库杂物；不影响分发，不影响他人克隆 |
| P3 | `pairctl.py` 巨石（3536 行 / 108 个顶层函数 / 23 个子命令） | 难以 review，agent 不敢整读，与 skill 的省 context 目标相悖 |

## 仓库概况

- 6 个 skill：`customer-delivery`、`customer-report-simplify`、`html-pdf-print`、`herdr-pair`、`gitee-pr`、`nextflow-workflow-skills`；99 个受版本控制文件，38 个提交，最后提交 2026-09-26。
- 仓库 `tcuni24/tc-skills` 为 **PUBLIC**。GitHub Issues 13 条全部 CLOSED，`AGENTS.md` 与 `docs/agents/` 三份 agent 约定齐备，中英 README 内容同步（均为 6 个 skill）。
- 缺失：`.github/`、`pyproject.toml`/`requirements*.txt`/`conftest.py`、`LICENSE`、根 `CONTEXT.md`。

## A. 明显问题

### A1. 公开仓库没有许可证文件（P0）

- `README.md` 末行："Distributed under the [MIT License](LICENSE)"，而 `git ls-files` 中没有 `LICENSE`，链接指向不存在的文件。
- `gh repo view --json licenseInfo` 返回 `null`：GitHub 也识别不到任何许可证。没有许可证文件的公开仓库，默认不授予他人使用、修改、再分发的权利，README 里的一句话不足以替代。
- `skills/nextflow-workflow-skills/SKILL.md` frontmatter 声明 `license: Apache-2.0`，但该目录内找不到任何上游来源、版权或署名信息。

一个仓库按目录采用不同许可证是常见做法，MIT 与 Apache-2.0 并存本身不构成矛盾；真正的缺陷是**缺 `LICENSE` 文件**，以及 nextflow 目录若是 Apache-2.0 派生作品，**缺来源与 `NOTICE` 署名**。许可证基调需要人类决策（见"待决问题"），定下后补 `LICENSE`，必要时为 nextflow 目录单独放许可证与来源说明，再同步 README。

### A2. 没有 CI（P1）

`.github` 目录不存在，仓库内也没有任何 Makefile / tox.ini / CI 配置（见附录 V2）。全套测试约 70 秒，靠记忆手工执行不现实。

CI 的价值在于守住**现有**用例已覆盖的行为，而不是拦截近期那类 bug：近期仅有的两条修复 `6def52b`（pairctl 未读 `HERDR_BIN_PATH`）与 `5b88c10`（pairctl 恢复应答后重新启用失败通知）都随修复**新增**了测试（分别 +36、+39 行），说明修复前测试并未覆盖它们，CI 同样拦不住。

落地前提：先完成 A3（Python ≥ 3.11、matplotlib、openpyxl），否则 runner 上第一轮就失败。测试未依赖 `pdftotext` 等外部二进制，CJK 字体相关用例用 mock 隔离，但**尚未在 GitHub runner 上实测**，首个 workflow 以实际运行结果为准。

### A3. 第三方依赖与 Python 下限未声明（P1）

`matplotlib` 与 `openpyxl` 被 6 个文件使用（`skills/customer-delivery/scripts/{plot_kit,xlsx_kit,audit_delivery}.py` 与 `skills/customer-delivery/tests/{test_plot_kit,test_xlsx_kit,test_audit_delivery}.py`），但仓库里没有任何依赖声明文件；测试中 `skipIf` / `skipUnless` / `importorskip` 命中数为 0（附录 V3），干净机器上 `pytest` 会在收集阶段因 ImportError 失败。

`skills/herdr-pair/tests/test_pairctl.py:20` 使用 `tomllib`，**运行测试**要求 Python ≥ 3.11；各脚本运行时的最低版本尚未逐一确认。README 与 SKILL.md 均未提及版本要求。

建议声明测试依赖与 Python 下限（`requirements-dev.txt` 或 `pyproject.toml` 均可）。对缺依赖是否改为 skip 需要权衡：CI 中应当让缺依赖直接失败，skip 只适合给本地"部分安装"场景兜底。

### A4. `create_gitee_pr.py` 的外部调用无超时 / 无禁交互兜底（P1）

- `skills/gitee-pr/scripts/create_gitee_pr.py:37-38`：`run()` 调用 `subprocess.run(cmd, capture_output=True, text=True)` 没有 `timeout`，所有 `git` 调用（含 `push`、`fetch`）经 `git()`（第 46 行）继承。stdin 未重定向，`git push` 需要凭据时会在终端等待输入；由 agent 非交互调用时表现为一直挂住。
- 同文件 `:313`：`with urlopen(request) as response:` 没有 `timeout`，连接建立后对端僵死时会无限等待。

修复建议分开处理：
- git 调用设置 `GIT_TERMINAL_PROMPT=0`（必要时 `stdin=subprocess.DEVNULL`），让缺凭据立即失败，这比超时更对症；
- 只给网络类 git 命令加宽松超时，不要在 `run()` 里一刀切，以免误杀正常的大仓库慢速 push；
- `urlopen` 加 `timeout`（如 30 秒）。

对照：`pairctl.py` 的 5 处 `subprocess.run`（`:931`、`:935`、`:1291`、`:1540`、`:2269`）中 4 处带 `timeout`（`:933`、`:937`、`:1292`、`:2275`），hooks 中的调用也都带 timeout。唯一裸调用 `pairctl.py:1540` 自我调用 `resume-deliver`，子进程内部调 herdr 走 `run_herdr`（带 `HERDR_CALL_TIMEOUT`），锁等待也受 `PAIRCTL_LOCK_WAIT_S` 约束，实际阻塞风险低，可顺手补上但不紧急。

### A5. `herdr-pair/reports/` 随 skill 包分发（P1）

`npx skills add tcuni24/tc-skills` 对 GitHub 源通过 git clone / GitHub API 取文件，**只会拿到受版本控制的文件**。因此：

| 位置 | 受控体量 | 是否随包分发 | 性质 |
| --- | --- | --- | --- |
| `skills/herdr-pair/reports/round-{9..16}-report.md` | 179,534 字节 / 8 文件 | **是** | 配对轮次记录，不是 skill 内容 |
| `skills/herdr-pair/tests/` | 231,666 字节 | 是（按 README 约定属于 skill 结构） | 设计选择，见待决问题 2 |
| `analysis/` | 工作树 448K / 28 文件 | 否（不在任何 skill 目录内） | 仓库杂物，见 C5 |
| `SKILL.md.bak*`、`.pytest_cache/`、`__pycache__/` | 0（被忽略，未受控） | 否 | 仅本地残留 |

`skills/herdr-pair/` 受控总量约 680K（工作树 `du` 约 1.6M，差额全是被忽略的本地文件）。真正需要处理的是 `reports/`：迁到 `docs/`（如 `docs/reports/herdr-pair/`），与 `docs/specs/`、`docs/adr/` 并列。

### A6. 同一份 herdr 可执行文件解析逻辑复制了三份，且已经漂移过一次（P2）

`PAIRCTL_HERDR → HERDR_BIN_PATH → 字面量 "herdr"` 这条解析顺序在三处各自实现：

- `skills/herdr-pair/scripts/pairctl.py:1275-1283`（额外以 `--herdr` 参数为最高优先级）
- `skills/herdr-pair/hooks/action.py:228-232`
- `skills/herdr-pair/hooks/notify.py:119-124`

`skills/herdr-pair/README.md` 还把这条顺序写成了对外承诺的规则。提交 `6def52b fix(herdr-pair): resolve herdr via HERDR_BIN_PATH inside pairctl` 正是漂移的直接后果：hooks 已按 issue #16 读取 `HERDR_BIN_PATH`，pairctl 漏了。建议两个 hooks 共用 `hooks/_herdr_bin.py`；pairctl 位于 `scripts/`，跨目录导入需评估插件进程的 `sys.path`，也可以让 hooks 调用 pairctl 暴露的查询子命令。README 那段指向唯一实现。

## B. 一致性问题（低成本可修，P2）

| # | 问题 | 证据 |
| --- | --- | --- |
| B1 | 参考资料目录命名不统一：`herdr-pair` 用单数 `reference/`，另有 4 个 skill 用 `references/`（`gitee-pr` 没有该目录） | `skills/herdr-pair/reference/` vs `skills/customer-delivery/references/`。`skills/herdr-pair/tests/test_skill_layout.py:22-25` 把单数形式硬编码进断言，改名需同步 |
| B2 | `agents/openai.yaml` 只有 2/6 个 skill 有 | 仅 `skills/gitee-pr/agents/openai.yaml`、`skills/html-pdf-print/agents/openai.yaml`；入口元数据应补齐或统一去掉 |
| B3 | frontmatter 语言混用 | `skills/html-pdf-print/SKILL.md:3` 的 `description` 全中文；其余 5 个以英文为主体（其中 3 个夹带中文触发词）。英文对话的 agent 对该 skill 的召回会变差 |
| B4 | 可执行位不一致 | `git ls-files -s skills` → 61 个 `100644` + 2 个 `100755`（`audit_report.py`、`create_gitee_pr.py`）。`herdr-plugin.toml` 用 `["python3", "hooks/…"]` 调用，不带 exec 位也能跑，但同仓库内应统一 |
| B5 | 指向仓库外 skill 的悬空引用 | `skills/herdr-pair/SKILL.md:3` "use herdr-handoff for those"、`skills/customer-delivery/SKILL.md:3` 引用 `tc-design-md` 白皮书风格——两者都不在本仓库，单独安装时是死链，至少应注明"外部 skill，需自行安装" |
| B6 | README 的 Repository Layout 已过期 | 只画了 `skills/<name>/{SKILL.md,references,scripts,tests}`，没有 `docs/`、`analysis/`，也没有 `herdr-pair` 特有的 `hooks/`、`herdr-plugin.toml`、`reference/`（单数）与插件安装路径 |

## C. 结构性优化（非紧急）

### C1. `pairctl.py` 已经是巨石（P3）

`skills/herdr-pair/scripts/pairctl.py`：3536 行 / 148,130 字节 / 108 个顶层函数 / 23 个子命令（`ack-round` 到 `watch-compact-continue`）。配套 `skills/herdr-pair/tests/test_pairctl.py` 4597 行 / 219,545 字节，也是 git 历史中最大的 blob（曾达 224,660 字节）。

对一个以"帮规划者省 context"为核心卖点的 skill（issue #2 专门为此设了预算），脚本侧却是一个 agent 不敢整读的 148K 单文件，这是最不划算的不对称。建议按命令域拆包：

```text
skills/herdr-pair/scripts/pairctl/
├── __init__.py     # 保留 pairctl.py 作为薄入口，保持既有调用路径不变
├── state.py        # load / save / persist / atomic_text / locked
├── notice.py       # record_notice / executor_notice_* / flush / stale 检测
├── context.py      # read_context_usage / derive_transcript / budget_for / write_checkpoint
├── handoff.py      # parse_handoff_fences / handoff_lint / snapshot_round / diff
├── rounds.py       # start/send/check/ack/finish/diff/adopt-contract
└── dispatch.py     # herdr_bin / run_herdr / resume-deliver / watcher
```

拆完行为不变、测试不动，可显著改善可 review 性与 agent 可检索性。附带提醒：`skills/herdr-pair/tests/test_skill_layout.py:19` 规定 SKILL.md `len(text) < 15000`，实测 13,825 字符（15,051 字节），只剩约 8% 余量——继续加内容前应先拆脚本或再下沉到 `reference/`。

### C2. 测试之间直接耦合（P3）

`skills/herdr-pair/tests/test_context_edges.py:9` 直接 `import test_pairctl as support`，并在 `setUp`/`tearDown` 里手工 `support.PairctlTest()` 再手调其 `setUp`/`tearDown`（第 13-18 行）。两个测试文件的构造与清理策略互相牵制，改其中一个就会波及另一个。建议把 `PairctlTest` 的夹具抽成 `tests/support.py`，测试类只继承不复用实例。

### C3. 单条测试 10 秒空等（P2）

全套约 70 秒，最慢用例 `test_startup_replays_saved_sidebar_view` 单条 10.36 秒，其余用例均在 1.9 秒以内。原因在测试侧：`startup_replay` 辅助函数（`test_pairctl.py` 约 4261 行起）的监听 socket 设了 `listener.settimeout(10)`，该用例后半段验证"没有保存视图时 hook 什么都不写"，这个反向断言要把 10 秒 accept 超时整整等满。

建议反向用例改为等 hook 进程退出后再非阻塞检查有无连接，而不是等满超时，预计可省约 10 秒。

注意：源码侧轮询间隔已经可注入（`pairctl.py:1621` 读取环境变量 `PAIRCTL_CONTINUE_POLL_S`），测试也已设为 `0.05`（`test_pairctl.py:900`、`:924`，`test_context_edges.py:164`），无需再改。其余 `pairctl` 用例约 1.2 秒主要是子进程启动开销；若要进一步提速可考虑 `pytest-xdist`，但它会引入新的测试依赖，需同步写进 A3 的声明。

### C4. 缺根 `CONTEXT.md` 术语表（P3）

`docs/agents/domain.md` 规定本仓库为 single-context 布局，以根 `CONTEXT.md` 承载领域术语，目前该文件不存在（策略允许惰性创建，不算违规）。但 Planner / Executor / Verifier、round、review epoch、`resume_pending`、handoff fence 等词已经跨 skill、spec 与 ADR 复用，补一份术语表成本低而收益明确。

### C5. 仓库卫生：`analysis/` 与 `.nfs*`（P3）

- `analysis/herdr-pair-hyf-20260921/`（一次性审计脚本 + `*.csv` + `*.json`）与 `analysis/issue-2-implementation/`（`*.log`、`snapshot*.json`、`COMMIT_MESSAGE.txt`，与 `docs/specs/issue-2-herdr-pair-context-budget.md` 内容重叠）受版本控制。它们不在 skill 目录内、不随包分发，只是仓库杂物；可迁入 `docs/` 或在确认无保留价值后删除。
- `skills/customer-delivery/.nfs0000000007c2842100002440` 是 NFS silly-rename 残留（内容是旧版 customer-delivery 的 `SKILL.md`，即文件在打开状态下被删除），目前靠 `.git/info/exclude:7` 的 `.nfs*` 隐藏。它**未受版本控制，他人克隆不会看到**；风险仅在于本机若未来丢失 exclude 规则，`git add -A` 可能误提交。建议在 `.gitignore` 补 `.nfs*`，成本几乎为零。
- `.pytest_cache/` 无需加入 `.gitignore`：pytest 会在该目录内自动写一个内容为 `*` 的 `.gitignore`，`git status --ignored` 已显示为忽略状态。
- 测试把临时目录建在 `tests/` 下（`tempfile.TemporaryDirectory(dir=…)`，共 5 处：`test_pairctl.py`、`test_skill_layout.py`、`test_plot_kit.py`）符合本机"不得写 `/tmp`"的规则，应保留。用例被强杀时可能在 `tests/` 下留下 `tmp*`、`plot-kit-*` 目录，可按需在 `.gitignore` 补 `skills/*/tests/tmp*/`、`skills/*/tests/plot-kit-*/`。
- `skills/herdr-pair/SKILL.md.bak*` 3 个文件已被 `*.bak*` 忽略，只是本地文件，清理与否不影响仓库。

### C6. 远端缺 3 个 triage 标签（P3）

`gh label list` 显示存在 `ready-for-agent`、`wontfix` 等，缺 `needs-triage`、`needs-info`、`ready-for-human`。`docs/agents/triage-labels.md` 本身已要求"贴标签前先 `gh label list` 并补建缺失标签"，目前尚未补。

## 建议行动顺序

| 优先级 | 动作 | 成本 |
| --- | --- | --- |
| P0 | 定许可证基调，补 `LICENSE`；nextflow 目录若为派生作品，补来源与 `NOTICE`；同步 README | 需决策 |
| P1 | 声明测试依赖与 Python ≥ 3.11（A3），随后加 `.github/workflows/test.yml` 跑 `python3 -m pytest -q`（A2） | 约半小时，以 runner 实测为准 |
| P1 | `create_gitee_pr.py`：git 调用加 `GIT_TERMINAL_PROMPT=0`，网络类命令与 `urlopen`（`:313`）加 `timeout` | 30 分钟 |
| P1 | `skills/herdr-pair/reports/` 迁入 `docs/` | 15 分钟 |
| P2 | 合并 herdr-bin 解析（A6）；统一 `reference/`→`references/`、`agents/openai.yaml`、exec 位；修 README Layout 与 B5 悬空引用；缩短 C3 反向用例等待 | 0.5 天 |
| P3 | `.gitignore` 补 `.nfs*`；`analysis/` 迁移或清理；补 triage 标签与 `CONTEXT.md` | 30 分钟 |
| P3 | 拆 `pairctl.py` 成包（C1）+ 测试抽 `support.py`（C2） | 1–2 天，分轮次做 |

## 待决问题（需人类决定，不宜由 agent 代答）

1. **许可证基调**：整仓 MIT，还是保留 nextflow skill 的 Apache-2.0 并按目录分别声明？nextflow 内容若派生自第三方，上游来源是什么？
2. **skill 包体边界**：`tests/` 是否随包分发？对 GitHub 源只有受控文件会被安装，所以"不分发"只能通过移出 skill 目录实现，`.gitignore` 无法做到"入库但不分发"。本地路径安装（`npx skills add ./path`）是否会带上被忽略文件未核实。
3. **`reference/` 是否统一为 `references/`**：涉及 `test_skill_layout.py` 断言、`skills/herdr-pair/SKILL.md` 内 11 处 `reference/…` 链接，属于对外可见路径变更。

## 附录：复核命令

所有结论可用以下命令在本仓库复现（均在仓库根执行）：

| # | 命令 | 预期观察 |
| --- | --- | --- |
| V1 | `python3 -m pytest -q --durations=5` | `183 passed, 69 subtests passed`，约 70 秒；最慢 `test_startup_replays_saved_sidebar_view` 10.36s |
| V2 | `ls .github; git ls-files \| rg 'pytest.ini\|pyproject.toml\|conftest.py\|requirements.*\.txt\|LICENSE'` | 全部为空 → 无 CI、无配置、无依赖声明、无许可证 |
| V3 | `rg -n 'skipIf\|skipUnless\|importorskip' skills/*/tests/*.py` | 0 命中 |
| V4 | `rg -n 'urlopen\(request\)\|subprocess.run\(cmd' skills/gitee-pr/scripts/create_gitee_pr.py` | `:38`、`:313` 均无 `timeout` |
| V5 | `gh repo view --json visibility,licenseInfo` | `PUBLIC`，`licenseInfo: null` |
| V6 | `for d in tests scripts reports hooks reference; do printf "%s " $d; git ls-files -z skills/herdr-pair/$d \| xargs -0 cat \| wc -c; done` | 受控体量；`reports` 179,534 字节 |
| V7 | `git status --short --ignored skills .pytest_cache` | `.bak*`、`.pytest_cache/`、`.nfs*`、`__pycache__/` 均为 `!!`（忽略，未受控） |
| V8 | `git check-ignore -v skills/customer-delivery/.nfs0000000007c2842100002440` | 命中 `.git/info/exclude:7:.nfs*` |
| V9 | `python3 -c "from pathlib import Path; print(len(Path('skills/herdr-pair/SKILL.md').read_text(encoding='utf-8')))"` | 13,825（上限 15,000） |
| V10 | `gh label list` | 缺 `needs-triage`、`needs-info`、`ready-for-human` |
| V11 | `git ls-files -s skills \| awk '{print $1}' \| sort \| uniq -c` | 61 个 `100644` + 2 个 `100755` |
| V12 | `rg -n 'PAIRCTL_CONTINUE_POLL_S' skills/herdr-pair` | 源码 `pairctl.py:1621` 读取，测试已设 `0.05` |
| V13 | `rg -n 'settimeout' skills/herdr-pair/tests/test_pairctl.py` | `startup_replay` 中 `listener.settimeout(10)` |
| V14 | `git show --stat 6def52b 5b88c10` | 两条修复均新增 `test_pairctl.py` 用例 |

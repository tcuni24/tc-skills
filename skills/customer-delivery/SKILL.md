---
name: customer-delivery
description: Use when an analysis is finished and the user asks to 生成交付结果 / 交付给客户 / 整理交付目录 / 做客户报告 / 出交付表格, or wants to turn work/ results into what the customer receives. Builds a delivery directory containing only an Excel workbook, an offline HTML report, and a plot/ directory; renames internal columns to customer terms; strips internal paths, file names, logs and pipeline details; renders the report in the tc-design-md whitepaper style; and audits the result for leaks and readability. Not for internal QC reports or methods docs.
---

# 客户交付包生成

分析跑完后，`work/` 里有几十个中间文件、日志、内部 TSV。客户只需要三样东西：
**一份 Excel、一份能离线打开的 HTML 报告、一个放统计图的 `plot/` 目录**。
本 skill 把分析结果整理成这个交付包，并保证表格和报告里没有内部文件、内部路径、内部分析细节。

依赖：`python3` + `openpyxl` + `matplotlib`（渲染器本身只用标准库）。
配套：报告文案遵循 `customer-report-simplify`（同仓库 skill），样式要点与配色规约详见
`references/report-style.md`（脚本 `render_report.py` 已内置该套样式）。

## 交付默认边界

| 交付 | 不交付（除非用户明确要求，或数据超出 Excel 上限） |
|---|---|
| `<项目>.xlsx`（首页为「说明」） | 原始 / 中间 TSV、BED、GFF、FASTA、比对结果 |
| `report.html`（离线、相对引用 `plot/`） | 日志、配置、脚本、conda 环境、测试目录 |
| `plot/*.png`（报告引用的统计图） | 内部 summary.txt、README 草稿、设计文档 |

"必要"的例外只有两种：表格行数超过 Excel 上限（改 CSV 并在说明页写明）、客户明确要求的原始格式文件（如 GFF3）。
加任何例外都要在报告「文件」章和 Excel 说明页列出来。

## 步骤

### 1. 盘点结果，写交付规格

先看 `work/final/`（或等价的最终结果目录）和项目需求文档，回答：

- 客户的问题是什么（通常一两句，写进报告标题下的速览）。
- 哪几个文件直接回答这个问题 → 进 Excel。
- 每个内部列名对应什么客户说法；哪些内部枚举值要翻译（HC → 高、`liftoff_unmapped` → 序列无法定位）。
- 哪些列是内部调试字段（中间分值、内部 note、路径）→ 不进 Excel，或只进附录字段对照。
- 要几张统计图（一般 3–5 张，每张回答一个问题）。

把这些写成项目里的一个 deliver 脚本（如 `scripts/04_deliver.py`），不要手工改表。
脚本是唯一权威：数据 → 统计 → 图 → xlsx → 报告 JSON → HTML，一次跑完。

### 2. 生成 Excel（`scripts/xlsx_kit.py`）

```python
sys.path.insert(0, "<skill>/scripts")
from xlsx_kit import Column, ReadmeSheet, tsv_to_sheet, write_workbook
```

- 第一页 `ReadmeSheet`：项目是什么、每张表放什么、每列什么意思、使用建议。客户不看报告也能用表。
- `Column(src, header, mapping, desc)`：src 是内部列名，只在代码里出现；header 与 desc 写进 Excel。
- 主表只放 HC/MC 这类"建议使用"的行；低置信、未对应放单独工作表并写明原因，而不是删掉。
- 缺失值留空，不写 NA / None / 0。
- 表名 ≤ 31 字、不含 `[]:*?/\`。

### 3. 画统计图（`scripts/plot_kit.py`）

`with plot_style(): …; save(fig, delivery/plot/<name>)`。
每张图先问"客户看这张图回答什么问题"；纯内部 QC 图不进交付。
中文标签前调用 `cjk_font()`。它检查 normal / semibold 实际解析到的字体文件、字体内部 family 和常用字形，
不会接受名称声称是 SC、实际打开却是 JP / KR / TC / HK 区域字形的 TTC。返回 `None` 时改用英文标签，
并显式使用 `plot_style(cjk=False)`；不要让 matplotlib 自行回退字体。

`save()` 会在写文件前生成刻度、图例和 colorbar，检查标题、坐标轴、图例、注释等可见中文文本
（包括中文与全角标点）的实际字体及字形覆盖；发现日文区域字形或缺字会直接报错。需要留内部复核记录时，可调用
`audit_fonts(fig)` 取得实际字体路径和 family，但不要把路径写进客户交付目录。

### 4. 写报告内容 JSON，渲染 HTML（`scripts/render_report.py`）

结构与块类型见 `references/content-spec.md`。写文案时执行 customer-report-simplify 的规则：

- 一个问题只用一种口径；核心数字全篇一致，来源同一份统计。
- 正文数字只放表格、KPI、图注；段落里用"约八成"。
- 术语第一次出现给中文解释；内部列名、英文枚举值、软件名只出现在附录里。
- 删掉防御性表述（"不是……而是……"、"您无需……"、"为什么我们没有……"）。
- 限制放在对应答案之后，用"使用时注意"。
- 附录全部 `collapse`，`appendix.id` 保持 `sec-appendix`。附录必须包含两节：
  - **方法说明**：分析流程几步、每步做什么、结果怎么判定，用客户语言写（`steps` + `kv`），软件名允许在这里出现；让客户能向别人转述"结果是怎么来的"。
  - **软件与版本**：一张表（软件 / 版本 / 用途）。版本号在生成时从实际运行环境取（运行 `--version` 或读环境清单），取不到就写"未记录"，不要凭记忆填。

```bash
python3 <skill>/scripts/render_report.py content.json -o delivery/<date>/<name>/report.html [--logo brand.png]
```

logo 可选：`--logo <brand.png>`，传入项目自有品牌图片文件；若省略则不渲染封面 logo。

### 5. 体检

```bash
# 1. 交付目录结构与信息泄漏体检（本 skill 内置脚本）
python3 <skill-dir>/scripts/audit_delivery.py delivery/<date>/<name> --terms <内部列名,内部枚举值,内部样本码>

# 2. 报告文本可读性与通俗度体检（依赖同仓库 customer-report-simplify skill）
# 定位方式：
#   - 仓库内同级引用：<skill-dir>/../customer-report-simplify/scripts/audit_report.py
#   - Agent 全局安装引用：<agent-skills-dir>/customer-report-simplify/scripts/audit_report.py
python3 <simplify-skill-dir>/scripts/audit_report.py delivery/<date>/<name>/report.html \
    --extra-terms <同一批术语>
```

`audit_delivery` 查：白名单外的文件、绝对路径、`work/` 片段、脚本 / 日志名、用户名主机名、
外部资源、缺图、自定义术语（xlsx 单元格也扫）。`audit_report` 查正文（附录前）的术语命中、
字数、数字密集句。两者退出码都要为 0；命中就回到 deliver 脚本改，不要手改产物。

两个审计脚本不识别 PNG 中汉字的地区字形。再用浏览器（或 `agent-browser`）打开 `report.html`，
看一眼封面、KPI、图是否显示，并人工核对图中的简体中文字形；无浏览器时如实说明未做。

### 6. 汇报

写清：交付目录路径与文件清单、每张表行数、审计结果（两个脚本的输出摘要）、
哪些内部文件被排除、哪些例外被放进来及原因、未做的验证。
不要复述分析方法。

## 目录约定

```
<project>/
├── scripts/04_deliver.py          # 项目专用 deliver 脚本（内容层）
└── delivery/<YYYYMMDD>/<项目编号>-<主题>/
    ├── <项目编号>-<主题>.xlsx
    ├── report.html
    └── plot/*.png
```

deliver 脚本内部产生的 `content.json` 放在 `delivery/<date>/_build/`（下划线前缀，不在交付目录内），方便复查。

## 反模式

- 直接 `cp work/final/*.tsv delivery/`：列名是内部的，客户看不懂，且 summary.txt 带着流程字段。
- 报告正文写"见 `work/final/xxx.tsv`"、"由 `03_integrate.sh` 生成"、"阈值 `OVERLAP_MIN=0.30`"。
- Excel 里保留 `note=cross_chrom`、`evidence=FBpS` 这类内部编码而不翻译。
- 把 4 个方向的比对结果都交付：客户只问了 A→参考，B→参考。
- KPI 四格全是成绩色；把"未对应比例"当成绩指标。
- 报告说"所有图片已内嵌"但实际用的相对路径；或反过来只交付 HTML 不带 `plot/`。

参考：`references/example-gene-map.md` 是一次实际交付（基因 ID 对应表）的规格与检查结果。

维护 `plot_kit.py` 后运行字体回归检查：

```bash
python3 -m unittest discover -s <skill>/tests -v
```

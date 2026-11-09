---
name: customer-methods-doc
description: Use when writing, reviewing, or unifying a customer-facing methods document in Word (方法说明 / 分析说明 / 方法与结果说明 / 更新说明 .docx) that accompanies delivered result tables, especially when several deliveries for the same customer must read consistently (same section skeleton, layout, wording, column names). Covers locating the script that generates the docx, reconciling every number with what was actually delivered, stripping internal details (paths, script and intermediate file names, GPU models, thread counts, placeholder read groups, draft highlights), unifying result-table column names with a single column-description source, a shared python-docx layout kit, and a checker script for leaks, table numbering and punctuation. Not for HTML delivery packages (use customer-delivery) or for simplifying an over-technical report (use customer-report-simplify).
---

# 客户方法说明文档（docx）的编写、审核与统一

客户拿到结果表后，还会收到一份 Word 版说明：怎么做的、用了哪些参数、表里每列是什么意思、这次更新改了什么。
本 skill 管这份文档：从生成脚本出发改内容，数字与实际交付对齐，去掉内部信息，
同一客户的多份说明在章节、版式、措辞、列名上保持一致，最后用检查脚本和人工阅读验收。

依赖：`python-docx`（版式模块）；检查脚本 `scripts/check_methods_doc.py` 只用标准库。
配套：骨架与共通措辞见 `references/doc-skeleton.md`；一次实际交付（草莓 + 香菜 SSR）的过程见
`references/example-ssr-two-species.md`。

## 原则

1. **改生成脚本，不手改 docx。** 文档和结果表由同一个脚本或同一组脚本生成，改完重新生成。
2. **事实以实际交付为准。** 数字、参数、文件名都要能在交付文件、交付 README / 清单或实际运行的脚本里找到出处。
   查不到出处的数字删掉，不推算、不凑数。
3. **说明写给客户。** 保留影响结果的参数和软件版本，去掉只对内部有意义的细节。
4. **多份交付一处定义。** 版式代码、共通段落、列说明各只维护一份，其余地方引用或逐字复用，并用 diff 校验。
5. **业务取舍交给用户。** 影响交付集合或结论的选择（例如校正后部分位点不再满足判据，是否仍保留）先问用户，不自行决定。

## 步骤

### 1. 定位与备份

- 在项目目录里 grep 文档标题或 docx 文件名，找到生成它的 `.py` 脚本，以及生成结果表（xlsx / tsv）的脚本。
- 用 pandoc 把现有 docx 转成文本阅读：`pandoc X.docx -t plain --wrap=none`（或 `-t markdown` 看标题和表题层级）。
- 动手前把交付物和生成脚本备份到非 `/tmp` 的目录（如 `/project/tmp/<任务>/backup_<日期>/`）。备份同时是回归对照和「应报错」样本。
- 运行生成脚本需要 `python-docx`。本机共享环境未必安装，不要往共享环境 `pip install`；
  使用项目里已 vendored 的包（`PYTHONPATH=<项目>/pylib`）或项目自己的环境。

### 2. 审核内容：逐项找出处

对每个数字和参数回答「它来自哪个交付文件或哪段代码」：

- **筛选漏斗要能算平。** 每一步写出「上一步数量 → 本步数量（去掉了什么、多少个）」，前后相减一致。
  中间表数字与最终交付不同时，以交付的 README / 交付清单 / 结果表为准。
- **参数照实际脚本写。** 例如特异性判据是「100% 一致且比对长度 ≥ 18 bp」，就不要写成「全长匹配」。
- **多份交付参数不同的地方照实写**，不要为了统一而改成一样。
- 发现与交付不一致、又无法从记录判断的地方，列出来问用户。

### 3. 去内部信息

| 去掉 | 保留 |
|---|---|
| 本地路径、内部文件名（`genome.fa`、`*.sorted.bam`、区域 BED、VCF 名）、脚本名 | 交付文件名、公开下载 URL 与公开文件名 |
| GPU / 服务器型号、线程数、`-num_threads`、`-outfmt` 等运行细节 | 软件名与版本（可写「GPU 加速版」这类与结果相关的版本说明） |
| read group 占位值（`LB=lib1`、`PL=ILLUMINA`；后者还可能与 DNBSEQ 等实际平台矛盾） | 影响结果的参数与阈值 |
| 「客户提供」这类第三人称、草稿提示句、黄色高亮 | 「提供的 N 个标记位置」这类中性表述 |
| 内部坐标转换、中间格式细节 | 结果怎么判定、客户能复述的步骤 |

### 4. 多份交付统一

- **章节骨架**：1 概述（含「表 1 分析流程总览」）/ 2 材料与数据 / 3 分析方法与参数 / 4 结果汇总 /
  5 交付文件与字段说明 / 6 使用建议与注意事项 / 7 软件与版本 / 参考文献 / 附录 本次更新内容。
  三级小节按各自流程展开。详见 `references/doc-skeleton.md`。
- **版式**：所有生成脚本使用同一份 `scripts/docx_kit.py`（`DocWriter`：微软雅黑、页边距 2.2 cm、标题色 1F3A5F、
  表头底色 DDEBF7、表题自动编号「表 N」并返回表号、短内容列整列居中、表头跨页重复、代码块灰底、参考文献悬挂缩进）。
  复制进项目时保持逐字一致，并用 `diff` 证明。

  ```python
  sys.path.insert(0, "<skill>/scripts")          # 或复制 docx_kit.py 到项目脚本目录
  from docx_kit import DocWriter, REF_BLAST, REF_MISA, REF_PRIMER3

  w = DocWriter()
  w.title("草莓（Fragaria × ananassa）SSR 标记开发", "方法、参数与结果说明", "——……")
  w.h("1 概述"); w.p("……")
  t_flow = w.table("分析流程总览", ["步骤", "内容", "软件/方法", "数量变化"], rows)  # 返回表号
  w.p(f"流程见表 {t_flow}。")
  w.code("primer3_core < input.txt"); w.bullet("……"); w.note("……")
  w.references([REF_MISA, REF_PRIMER3, REF_BLAST])
  w.save(out_docx)
  ```
- **措辞**：共通段落（产物大小为理论值、stutter 提示、欢迎反馈等）逐字复用；术语统一
  （重复次数、单纯型 / 复合型、特异 / 非特异）。
- **格式**：中文语境用全角标点；数值范围用「–」（不用「~」或「--」）；百分比保留 1 位小数；
  文件名统一为「<物种>SSR标记开发方法与结果说明.docx」这类「<对象><主题>方法与结果说明.docx」。
- **结果表列名**跨交付统一（英文 snake 风格，例如 `*_size_bp`、`size_min_bp`、`ref_size_bp`）。
  列说明只维护一份（如脚本里的 `COLS` 列表），xlsx「说明」页和 docx 字段表都从它生成。
- 改列名或数值逻辑后做回归：与备份逐格对比数据页，除有意改动的列外数值不变；
  有测试的脚本先写测试看到红灯，再改实现转绿。

### 5. 交叉引用

正文里写死的「见表 N」「见 3.9 节」在加表、删表或调整章节后会错位。用 `DocWriter` 返回的表号拼字符串。
表号与表引用由检查脚本检查；调整结构后，「见 3.9 节」等章节引用须人工核对。

### 6. 验证

```bash
# 内部信息、表号连续、引用越界、高亮残留、半角标点（ERROR 时退出码为 1）
python3 <skill>/scripts/check_methods_doc.py A.docx --allow <可出现的交付文件名/公开文件名/软件脚本名> \
    [--terms <内部样本码,项目代号>] [--compare B.docx]
```

- 检查范围包括正文、表格、页眉页脚、脚注和尾注的文字与高亮。
- `--allow` 只精确放行完整文件名，如交付文件名、公开下载文件名和软件自带脚本名（如 `misa.pl`），不影响路径等其他类别。
- `--compare B.docx` 按标题文字（忽略章节号和括号内容）对齐两份文档的标题结构，不一致或一侧独有的行以 `≠` 标出；物种特有小节的差异是正常的，逐条确认即可。
- WARN 不影响退出码。表头里的单位写法如「总长(bp)」「比对率(%)」会报半角括号；同一客户各份文档写法一致即可，正文句子里的半角标点要改。
- 把修改前的备份 docx 也跑一遍，确认检查脚本能报出已知问题，避免「全绿」只是因为没查到。
- 检查脚本只查文字，查不出事实错误；第 2 步的出处核对必须人工完成。
- 版面（分页、表格宽度、字体回退）需要在 Word 里看。本机没有 LibreOffice 时，请用户用 Word 打开确认，并在汇报里写明未做版面渲染检查。

### 7. 汇报

写清：交付目录与文件名、改了什么（按「内容 / 去内部信息 / 统一 / 列名」分组）、
检查脚本输出摘要（新旧版本 ERROR / WARN 数）、回归对比结果、需要用户决定的事项、未做的验证。

## 反模式

- 直接在 Word 里改交付 docx，下次重新生成时改动丢失。
- 用中间表的数字写漏斗，结果与交付表对不上；或为了让漏斗算平而推算出一个查不到来源的数。
- 同一客户两份说明，一份写「重复数」一份写「重复次数」，一份用「~」一份用「–」，字段名一份 `Product_size` 一份 `ref_size_bp`。
- xlsx 说明页和 docx 字段表各写一遍列说明，后来只改了其中一处。
- 删了一张表，正文仍写「见表 9」。
- 方法说明里出现服务器 GPU 型号、`-num_threads 16`、`genome.fa`、`LB=lib1`。

## 文件

```
customer-methods-doc/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── doc-skeleton.md               # 章节骨架、共通措辞、去内部信息清单
│   └── example-ssr-two-species.md    # 实例：草莓 + 香菜 SSR 两份说明的审核与统一
└── scripts/
    ├── check_methods_doc.py          # docx 体检（标准库）
    └── docx_kit.py                   # 共享版式 DocWriter（python-docx）
```

测试在仓库 `tests/customer-methods-doc/`。

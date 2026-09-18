# 实例：TC-WGS-20260728-ZL002 水稻基因 ID 对应表交付（2026-09-18）

需求：把 xiangfu1822、Osf、Nipponbare 三套注释的基因 ID 对应到 R498 参考基因组。
分析结果在 `work/final/`：三组 `gene_map.tsv` / `unmapped.tsv` / `summary.txt`、`group_table.tsv`（带置信度）和 `r498.idmap.tsv`（4 列简表），
共 14 个内部列名、4 类英文未对应原因码、`FBPS` 式证据编码、`cross_chrom` 备注。

deliver 脚本：`<project>/scripts/04_deliver.py`（统计 → 5 张图 → 8 页 Excel → 报告 JSON → 渲染）。
输出：`delivery/20260918/TC-WGS-20260728-ZL002-基因ID对应/`（xlsx、report.html、plot/ 5 张 PNG）。

## 交付 / 不交付

| 交付 | 排除 |
|---|---|
| Excel：说明 + ID 对照简表 + 对应总表 + 三张「品种 → R498」+ 低置信候选 + 未对应基因 | `work/prep/`（BED、蛋白、索引）、`work/liftoff/`、`work/diamond/`、`work/logs/` |
| report.html | `summary.txt`（带流程字段）、`config.sh`、脚本、`test/`、`docs/*.md` |
| plot/01–05 | 双向比对中的 R498 → 品种方向（客户没问） |

## 内部 → 客户说法

| 内部 | 客户 |
|---|---|
| `tier` HC / MC / LC | 置信度 高 / 中 / 低 |
| `relation` 1:1 / 1:N / N:1 / N:M | 对应关系 一对一 / 一对多 / 多对一 / 多对多 |
| `evidence` `FBpS` | 支持证据「位置(正向) + 位置(反向) + 蛋白(单向) + 共线性」 |
| `note` `cross_chrom` | 备注「跨染色体」 |
| `lifted_no_ref_gene` | 能定位到 R498 坐标，但该位置没有注释基因 |
| `liftoff_unmapped` | 序列无法在 R498 上定位 |
| `low_confidence_only` | 只有低置信候选（见「低置信候选」表） |
| `no_evidence` | 没有任何对应证据 |
| idmap 里的 `-` | 空单元格 |
| `fwd_overlap` `rev_overlap` `liftoff_cov` `liftoff_seqid` `synteny_support` | 不进 Excel（判读用不到） |
| 正文里的「共线性」 | 「邻近基因排列」；「共线性」只留在附录 |

## 报告结构

| 章 | 内容 |
|---|---|
| 封面 | 速览一句话结论；KPI：三个基因组各自对应比例 + 风险位（R498 基因三方都有对应的比例） |
| 01 一页结论 | 结论框（含 Nipponbare 比例低的原因一句话）+ 3 行概览表 + 「请您确认的一件事」 |
| 02 对应结果 | 构成条 ×3 + 图 2.1 构成 + 图 2.2 染色体分布 + 关系类型表 + 图 2.3 + 跨染色体警示 |
| 03 未能对应的基因 | 4 原因 × 3 基因组表 + 图 3.1 + 使用时注意 |
| 04 如何使用对应表 | 四步 + 8 张工作表一览 + 三级置信度卡 |
| 05 交付文件 | 文件、大小、SHA256 前 16 位 |
| 06 附录（折叠） | A 方法说明（五步流程 + 定级规则，客户语言） B 软件与版本（运行时从 conda 环境取版本号） C R498 覆盖 + 图 6.1 D Excel 列说明 E 术语 F 限制 |

方法说明与软件版本是附录必备两节：方法说明让客户能转述"结果怎么来的"；版本号由
`collect_versions()` 对 `env/gene-map/bin/*` 运行 `--version` 取得（Liftoff 1.6.3、minimap2 2.31、
DIAMOND 2.2.7、gffread 0.12.9、bedtools 2.31.1、samtools 1.24、Python 3.11.16），取不到写「未记录」。

## 检查结果

- `audit_delivery.py --terms <26 个内部词>`：7 个文件全在白名单，无路径 / 目录 / 脚本名 / 术语命中。
  早期版本命中 `tier_summary.png`、`relation_types.png` 文件名 → 改为 `01_mapping_overview.png`、`03_mapping_types.png`。
- `audit_report.py`：正文 5,039 字，术语命中 0，缺图 0，6 个折叠块。
  早期命中默认术语「共线」→ 正文改「邻近基因排列」。
- 无头浏览器（agent-browser，`--args --no-sandbox`）：7 张图全部加载，1280px 宽版排版正常；
  科学图迭代过两处（网格压柱 → `axes.axisbelow`；染色体图 3 组柱数字重叠 → 标签旋转 90° + 加高 y 轴）。
- 未做：打印分页预览、窄屏（≤680px）实际截图。

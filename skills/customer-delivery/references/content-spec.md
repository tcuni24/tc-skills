# render_report.py 内容规范（JSON）

渲染器只排版，不算数。所有数字、比例、结论由内容层（项目的 deliver 脚本）写进 JSON。

## 顶层

```json
{
  "lang": "zh-CN",
  "genre": "结果交付报告",
  "title": "报告标题",
  "subtitle": "一句话副标题",
  "intro": {"eyebrow": "速览", "title": "一句话结论", "text": "规模说明 + 阅读入口"},
  "meta": [{"label": "项目编号", "value": "…"}, {"label": "参考基因组", "value": "…"}],
  "kpis": [{"value": "83.6%", "label": "指标名", "note": "口径说明", "watch": false}],
  "sections": [ {"id": "overview", "title": "一页结论", "short": "结论", "kicker": "OVERVIEW", "blocks": [ … ]} ],
  "appendix": {"id": "sec-appendix", "title": "附录", "short": "附录", "blocks": [ {"type": "collapse", …} ]},
  "footer": {"left": "项目名", "left_sub": "生成日期", "right": "版权 / 联系方式"}
}
```

- `kpis` 通常 4 格；`watch: true` 表示风险位（琥珀色），不要把成绩指标标成 watch。
- `appendix.id` 保持 `sec-appendix`，`customer-report-simplify/scripts/audit_report.py` 默认以它为正文边界。
- `meta` 建议 3–4 项身份字段：项目编号、物种 / 参考、样本或品种、交付日期。

## 块类型

| type | 字段 | 渲染 |
|---|---|---|
| `p` | text, html? | 段落，支持 `**加粗**` |
| `lead` | text | 章节引导段 |
| `list` | items[], ordered? | 无序 / 有序列表 |
| `conclusion` | label?, title, text | 结论框（回答"结论是什么"） |
| `watchbox` | title?, text | 边界框（回答"结论用到哪里"），放在答案之后 |
| `note` | text, warning? | 口径补充；warning 为需注意的限制 |
| `panel` | title?, blocks[] | 小节容器 |
| `h4` | text | 小标题 |
| `table` | columns[{header,key,number?,html?}], rows[{}], caption?, dictionary? | 表；number 列右对齐；caption 自动编号 表 n.m；dictionary 首列等宽字体 |
| `kv` | items[{label,value}] | 参数 / 键值条目（两列） |
| `steps` | items[{title,text}] | 步骤条（自动 01/02…） |
| `cards` | items[{title,text,tone: good/warn/bad/neutral}] | 2–4 张分档卡 |
| `bars` | items[{title,total?,segments[{label,pct,color: green/teal/amber/red/gray}]}] | 构成条，pct 之和≈100 |
| `chart` | title, image, principle?, interpretation?, caption?, alt? | 图表卡，自动编号 n.m 与 图 n.m；image 用相对路径 `plot/x.png` |
| `grid2` | left[], right[] | 两栏并排 |
| `collapse` | summary, blocks[], open? | `<details>` 折叠块（附录用） |
| `files` | items[{name,desc,size?,sha256?}], caption? | 文件清单表 |
| `html` | html | 原始 HTML（内联 SVG 示意图用） |

图编号按章：第 2 章第 1 张图是 `2.1` / `图 2.1`。附录算最后一章。

## 常用骨架（customer-report-simplify 验证过）

1. `overview` 一页结论：conclusion + 最小表 / cards + watchbox（客户唯一要确认的事）
2. `q1` 客户问题一：一句话定义 + 表 + 1–2 张图 + 使用注意
3. `q2` 客户问题二 / 怎么用：steps + 表
4. `files` 文件清单：files 块
5. 附录（全部 collapse）：方法说明（流程步骤 + 判定逻辑）、软件与版本表（版本从实际环境取）、其他口径、字段对照（dictionary 表）、术语表、限制清单。方法说明和软件版本是必备两节。

## 图片

- 报告与 `plot/` 同目录交付；HTML 中只用相对路径。
- 图片缺失时页面显示占位框和预期路径，渲染器退出码 1。
- 需要单文件交付时，把 `image` 换成 `data:image/png;base64,…`（内容层自行内嵌）。

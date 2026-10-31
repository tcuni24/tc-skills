# 2026-10-31 并行性能验收与后续收尾

Herdr/Devin 并行处理三项 deliver 性能任务，以下为当时验收，不等于部署或当前线上状态：

- web !470，HEAD `49824bf`：Vue chunk raw 1,354,214→211,995 B，gzip 416,550→76,729 B；11 项专用测试通过。比较的是分组资源，不是总流量或端到端加速；全量 998 通过、3 失败且无完整 base 对照，未覆盖有任务行的 BSA 列表。
- web !469，HEAD `0b0fade`：5 个组件按需加载导出库，同轮浏览器导出 5/5，PNG fixture 字节一致，PDF 仅验证头尾和大小；全量 987 通过、3 失败。真实组件使用合成 fixture，未覆盖完整业务路径。
- backend !402，HEAD `8720918`：相关测试 67 通过、受影响测试 170 通过 3 skip；隔离 PostgreSQL 15 的 marker 实现与独立对照分别 27/28 种 region 通过。PG 专属测试未纳入 SQLite CI；特殊嵌套或转义数据的 Python 候选仍可能增长，不能声称绝对内存有界。独立审查两次要求修改后最终 PASS。

初始验收时三个 PR 为 OPEN；后续授权收尾记录称三者分别以 `ee774f9`、`6eed20c`、`efd9c34` 合并，四个实现/审查任务的本地 worktree、分支和 tab 已清理。Gitee CI 状态当时 unknown。后续收尾记录的备份清单分别为 39、903、43、18 项，清理前后哈希核对通过；远端源分支保留。原始命令、环境与日志位于本机归档 `2026-10-31-devin-perf-parallel/` 和 `2026-10-31-perf-cleanup/`，见[索引](README.md)。依赖外部项目、PG 和历史 fixture 的复现命令以这些原日志为准，当前仓库不能独立重跑。

实测还发现输入回显中的回报模板被误认，形成 #48。初始并行验收阶段最后一次 doctor 因一条 pending 通知 exit 1，同轮 `fleet wait` 后 idle/exit 0；wait 通过不能代替该阶段 doctor 通过。后续授权收尾后的 doctor 为 exit 0、pending=0，见归档 `2026-10-31-perf-cleanup/root-receipts/doctor-after.json`。旧事件 superseded 的自然路径通过，不代表覆盖所有竞争窗口。

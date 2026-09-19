# Gitee PR Rules

## 1. 核心原则

- 一事一分支：每个独立修改任务使用独立分支。
- 单一文件原则：原则上，一个分支只修改或新增一个核心脚本（配置文件或文档除外）。
- 先测后提：提交 PR 前确保本地结果可验证。当前自动化门禁为“工作区最终干净”。
- PR 使用中文：PR 标题和描述需包含中文内容。

## 2. 分支命名规范

格式：`成员名/动词-描述`

示例：
- `zhangsan/add-qc-script`
- `lisi/fix-bam-filter`
- `wangwu/update-readme`
- `zhaoliu/refactor-variant-call`

## 3. Commit 规范

允许前缀（支持可选的 `(scope)` 和破坏性变更标记 `!`）：
- `feat:` / `feat(scope):` / `feat!:`
- `fix:` / `fix(scope):` / `fix!:`
- `docs:` / `docs(scope):`
- `refactor:` / `refactor(scope):`
- `style:` / `style(scope):`
- `test:` / `test(scope):`
- `chore:` / `chore(scope):`

示例：
- `feat: 添加质控过滤脚本`
- `feat(probe): 支持自定义探针阈值`
- `fix: 修复内存泄漏问题`
- `fix(parser)!: 重构解析器返回值结构`
- `docs: 更新使用说明`

## 4. 标准流程

### 步骤 1：创建分支

```bash
git checkout main
git pull origin main
git checkout -b 你的名字/描述
```

### 步骤 2：开发与提交

```bash
git add <files>
git commit -m "fix: 修复XXX问题"
git push origin 你的名字/描述
```

### 步骤 3：创建 PR

1. 打开 Gitee 仓库，进入 Pull Request。
2. 源分支选择你的开发分支。
3. 目标分支选择 `main` 或 `master`。
4. 按固定模板填写 PR 描述并发起审核。

PR 描述必须使用以下模板（顺序不可变，每段必须有内容）：

```markdown
## 改动了什么？

## 为什么改动？

## 测试结果？

## 注意事项
```

## 5. 常见问题

Q: 需要同时改脚本和配置文件怎么办？  
A: 允许同分支提交脚本及其配套 config/README，但核心逻辑改动应保持集中。

Q: 合并后分支怎么处理？  
A: 合并后删除本地和远程分支。

```bash
git branch -d 分支名
git push origin --delete 分支名
```

Q: 发现提交有问题可以修改吗？  
A: PR 未合并可继续在同分支提交并推送；已合并则新建分支修复。

## 6. 管理员职责

- 定期审查待合并 PR。
- 确保代码质量和规范符合要求。
- 合并后提醒删除分支。
- 定期清理已合并的远程分支。

---
生效日期：2026年2月13日  
维护者：生信团队  
最后更新：2026年2月21日

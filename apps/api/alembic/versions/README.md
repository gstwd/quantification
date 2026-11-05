# Alembic 迁移版本

迁移链以本目录中的文件为准，用 `alembic history` 查看完整顺序，用 `alembic current` 查看数据库所处版本。
迁移文件本身即变更记录，本文件不另行维护迁移清单。

早期迁移中出现的 ETF 相关表已由后续迁移全部删除，系统不研究 ETF；`quant_etf` 等名称是兼容遗留，不代表 ETF 功能。

## 新建迁移

```bash
cd apps/api
alembic revision --autogenerate -m "description"
alembic upgrade head
```

迁移文件自动生成后需人工审查，确保字段类型、约束、默认值正确后再提交。新增时间戳列使用
`DateTime(timezone=True)`；破坏性操作（删表、删列、改类型）须先说明影响，详见 [AGENTS.md](../../../../AGENTS.md)「变更卫生」。

# 「我们」项目约定

## Bug 修复流程

每个线上/本地故障修复并验证通过后，必须按 `docs/BUG记录.md` 里的固定格式（日期/环境/现象/根因/修复/验证/预防）补登一条记录，编号递增。

## 环境速查

- 开发：`npm run dev`（uvicorn :8000 + vite :7100）
- 测试：`cd server && .venv-mac/bin/python -m pytest tests/ -q`（Mac 用 `.venv-mac`，Windows 用 `.venv-win/Scripts/python`，`.venv` 是残留）；smoke：`.venv-mac/bin/python scripts/smoke_test.py`
- 编译检查：`npm run build && npx tsc -b`
- 硬约束：依赖白名单制——除 requirements.txt 既有项外仅放行 `langgraph`、`langmem`、`langgraph-checkpoint-sqlite`、`langchain-openai`（含其传递依赖 langchain-core 等，见 requirements.txt 注释），其余一律不装；生产代码无 mock 模式，测试的确定性桩统一在 `server/tests/fakes.py`，由 conftest autouse 拦在 `app/ai` provider 层之后，测试不得触网

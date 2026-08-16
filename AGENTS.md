# 「我们」项目约定

## Bug 修复流程

每个线上/本地故障修复并验证通过后，必须按 `docs/BUG记录.md` 里的固定格式（日期/环境/现象/根因/修复/验证/预防）补登一条记录，编号递增。

## 环境速查

- 开发：`npm run dev`（uvicorn :8000 + vite :7100）
- 测试：`cd server && .venv-mac/bin/python -m pytest tests/ -q`（Mac 用 `.venv-mac`，Windows 用 `.venv-win/Scripts/python`，`.venv` 是残留）；smoke：`.venv-mac/bin/python scripts/smoke_test.py`
- 编译检查：`npm run build && cd weapp && npx tsc --noEmit`
- 硬约束：零新依赖；`weapp/` 已冻结不许动；`src/core/` 双端共享，改动须保持 weapp 编译通过；测试强制 mock 模式

# 测试与验证边界

安装 `requirements-dev.txt` 并使用 `requirements-lock.txt` 约束版本后，在根目录执行：

```shell
python -m unittest discover -s tests -v
```

标准测试使用临时数据库、合成样本、受控模型传输与真实受控 worker，不需要 API Key，不发付费请求。前端在 `frontend/` 执行 `npm ci`、`npm test` 和 `npm run build`。

| 文件 | 主要覆盖 |
| --- | --- |
| test_demo.py | 导入、失败加过期、历史可见性、SN 冲突、作业口径 |
| test_api.py | 严格参数、只读预览、幂等保存、组合筛选及分页、Host/Origin、存储错误 |
| test_storage_boundary.py | 值绑定、非法表/列/顺序拒绝、无效布局不创建数据库 |
| test_query_plans.py | 发现完整性、稳定成员、覆盖、确认 CAS、会话/期限/契约束 |
| test_evidence_execution.py | 真实 worker、截止/取消回收、有限重试、来源失败、重启与持久化 |
| test_fact_registry.py | 账本及语义校验、强制事实、遗漏、篡改和收据 |
| test_publications.py | 固定模板、零行和正常发布、明细、资格变化与回滚 |
| test_adversarial_matrix.py | 当前 LOT 意图的对抗断言、容量、分页和注入 |
| test_model_planner.py | 受控协议、有限句式、排除/否定/质量条件、身份与时间绑定 |
| test_model_evaluation.py | 评测工具收据与用量汇总 |

前端七项测试覆盖返工标签和请求守卫：查询条件变更、旧成功/失败晚到、新请求先完成等交错下不显示旧结果。

## 已记录结果

Windows x64 / Python 3.13.14：后端 **172 passed、172 subtests passed**，语句覆盖率 **2415/2749 = 87.85%**。前端在 Node 26.4.0 / npm 11.17.0 上 **7/7**，TypeScript/Vite 构建成功。

Ruff、mypy、Radon、Vulture、pytest/coverage 和 DeepSec L1/L2 提供检测，cq 汇总。本地 target A 门通过：无 blocker/high、无 D/E/F、超过原 80% 覆盖率阈值，未新增豁免。仍有 **15 项中等、200 项低级**静态发现；import-linter 无架构契约配置而跳过。近似评级为安全 A、可靠性 A、可维护性 B，不能把门禁 target A 写成所有维度均 A。

修复前后另比较 374 个确定性观察，覆盖包装、历史追溯、邻批、发现分页和集中性；规范化结果逐字节相同。这不能独立证明业务定义正确。

## 限制

- T01–T26 适用性见 [对抗测试范围](adversarial-acceptance.md)，不是 26 类全部通过。
- 当前 `nl-planner-3` 语法使用受控模型回归；历史付费调用不代替当前版本新付费复测，不推断任意语言准确率。
- 新 Python/Node 版本、Linux/macOS、真实满盘/断电、企业认证和生产压力不在上述结果内。
- 后端测试不代替实际 UI 布局、完整入口、下载及浏览器故障矩阵；前端单测也不代替浏览器验收。
- 公开源码包含可重跑测试和合成语料；本地运行日志、供应商原始响应和数据库不随源码分发。

`scripts/evaluate_model.py --help` 列出收费评测选项。必须显式带 `--live-model` 才发请求并要求新输出目录，建议放在忽略的 `reports/` 中。输出保留本地原始响应及数据库，不能加入版本控制。标准回归未调用收费模式。

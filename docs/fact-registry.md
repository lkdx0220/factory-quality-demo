# 事实注册与引用校验

范围仍仅为 `lot_operation_inventory`。查询成功后生成服务端事实集，校验候选引用是否
支持原确认范围的全部必需事实。事实层不提供模型自由回答或质量处置；最终发布由独立的 [发布层](answer-publication.md) 承接。
所有事实和校验收据保持 `answer_published=false`。

## 两道检查

G2 重新检查持久 completed 账本、两项来源的 DAG/逻辑调用/尝试、回收状态、
真实起止顺序、来源与结果摘要、请求/计划/快照/集合/范围/契约束身份及完整规范行。
它不只相信 的 completed 标记，也不只相信计数或重算后的摘要。
来源结果与已验证的固定快照再次比较，少行且同步缩小计数也不能通过。
执行中、失败、取消、中断或终态尚未持久化的执行不能生成完整事实集。

G3 校验候选引用：所有必需事实必须恰好出现一次，叶子值与类型、实体/作业、
历史时刻、指标/规则版本、单位及计数域都须匹配。请求、执行、计划、范围、快照、
集合版本、契约束与事实集摘要也必须匹配；同数字不表示同事实。
引用重排可以通过，删项、重复、改值、换实体/口径或借用其他请求事实均拒绝。

这些检查是当前切片的 G2/G3，不等于 G1–G4 全链通过，也不等于理解自然语言一定正确。

## 事实与来源

每个事实只包含一个标量叶子：string / integer / boolean / null，严格拒绝隐式转换。
`fact_id` 为规范事实内容摘要，包含当前请求/执行/计划、范围、快照、契约束、
实体、时刻、指标语义、值与来源链；稳定事实键使用记录 ID 和字段名，未使用数组下标。
同一持久执行重复生成/重启读取保留身份；新请求即使得到同一个数值，也有不同事实身份。

源事实记录 `source_leaf`：证据 ID、来源、record_id、有限字段和记录/范围位置。
派生事实记录注册的算法 ID、输入事实身份以及完整来源证据及摘要。
计数和集合算法可以处理完整后端集合，但候选引用不能指向数组或对象，不能自带 evidence，
不能执行任意 fact_path、通配、路径表达式、SQL 或计算代码。
本切片只接受 `fact_id` 加明确标量语义，不提供任意 evidence 路径接口。

| 必需摘要 | 来源 / 算法 | 单位或边界 |
| --- | --- | --- |
| lot_id | lots 的 LOT 主键叶子 | identifier |
| as_of | operations 证据绑定的历史截止 | UTC；不能用 executed_at 替换 |
| operation_count | count_unique_operations_v1，全部作业 ID 输入 | operation_record，非单件/尝试/版本数 |
| member_set_digest | canonical_members_digest_v1，全部规范行叶子 | SHA256，绑定确认集合 |
| membership_complete | complete_membership_v1 | boolean，完整来源与确认成员相等 |
| exclusion_count | bound_exclusions_count_v1 | 显式排除条目；首个意图不加业务筛选，为 0 |
| metric_applicability | inventory_applicability_v1 | NOT_APPLICABLE_OPERATION_INVENTORY |

每次可见作业还强制注册 operation_id、lot_id、process_step、equipment_id、
track_in_at、track_out_at、recorded_at 共 7 项叶子事实。正常 LOT-0001 示例共 35 项；
零作业仍有 7 项摘要，计数为 0、成员完整为 true，不把空集合当执行失败。
未来出站时刻仍沿用可见性规则隐藏为 null；本次固定样本未包含可见但尚未结束的作业案例，
该分支未据此新增实际浏览器验收主张。

`required_groups` 按已确认意图展开身份、时刻、作业数、完整成员、排除和适用性。
全部事实都是必需事实，模型或 HTTP 调用者不能只选摘要或代表性作业。
计数域为 `all_visible_operation_records`；身份/时刻/排除/适用性中无需分母的项明确
`not_applicable`。当前不支持质量意图，不造 expected/passed/failed/missing 等数值。

三完整性分别记录：execution=complete；business_evidence=not_applicable 并附原因；
presentation 说明完整注册表可由同一事实集分页和导出访问。页面 7 项摘要不代替全量事实。

## HTTP 与持久化

前缀 `/api/v1/query-plans`，沿用会话归属、Host/Origin、no-store 及严格参数约束。

| 方法 / 路径 | 行为 |
| --- | --- |
| POST `/executions/{id}/facts` | 严格空对象；从持久执行生成/复用不可变事实集 |
| GET `/fact-sets/{id}` | 重验前置、G2 与全部事实，返回摘要、必需身份集合与最新收据 |
| GET `/fact-sets/{id}/members` | fact_set_digest 必填，offset 0–100000，limit 1–100；全部事实分页 |
| GET `/fact-sets/{id}/proposal` | 服务端为全部必需事实生成规范候选；当前 UI 正控使用，不是模型输出 |
| POST `/fact-sets/{id}/validate` | ClaimProposal：完整绑定、事实集 ID/摘要和标量 claims；未知字段拒绝 |
| GET `/fact-sets/{id}/export` | 实际 JSON 附件，含完整事实/来源链/必需集合、最新收据、当前视图及严格 Schema |

Schema 定义在 `fact_contracts.py`，算法在 `fact_registry.py`，持久服务在 `fact_service.py`。
注册表、算法版本、Schema、来源语义、实际 UI 与依赖均进入内容寻址契约束。
单个事实集上限 2000 项；超限停止，不把节选当成全部，也不提供静默降级。

事实集和校验收据在原控制库新增 `fact_sets` / `fact_validations`；不写业务库。
事实只追加一次，已有事实与重新推导结果不一致时停止，不自动改旧集合。
每个读取、分页、候选、校验与导出都重核当前权限/有效期/快照/覆盖/精确束、持久执行与来源。
所有当前前置和 G2 检查在单进程共享锁及事务中完成；沿用单 API 进程约束。

校验收据包含 proposal_digest、绑定、G2/G3 结果、必需/提交/缺失数量、已验证事实身份和固定问题码。
原始候选值和模型自由文字不存入收据，不回显被拒内容；错误候选不会覆盖已注册的源事实。
收据事务写失败返回存储错误并回滚，不能把未保存的 accepted 结果回给调用者。

HTTP `ok=true` 只表示校验操作正常返回，必须读取 `accepted/g3_passed`。
被拒候选仍可导出正确的后端事实和 rejected 收据，不能误解为接受了候选。
现有事实视图/账本均不是 Publication；只有独立的 发布制品可代表正式答案。

## 界面与验收边界

确认计划→执行→刷新终态后，页面出现事实面板。可生成摘要、分页浏览全部事实、
校验规范候选、刷新资格和实际下载。权限/依赖/存储错误时停止显示面板中的旧事实与收据；
请求守卫随父范围编辑/切页卸载而失效。通过状态明确标注“事实/引用阶段记录”，不写产品合格；正式答案使用独立区显示。

`tests/test_fact_registry.py` 覆盖正常/空集合、逐一删除全部 35 个必需引用、错语义、
跨请求和跨会话、未知/重复事实、非标量/类型/自由文本、漏证据/错尝试/未确认回收、
重算摘要后的篡改、事实存储篡改、撤销/过期、重启、实际收据 INSERT 后故障回滚与历史边界。
浏览器验证正常四页和实际下载、错误候选拒绝及不回显、零作业通过。
这些是当前意图的限定验证，不是 T01–T26 全矩阵、实际模型或跨平台验收。

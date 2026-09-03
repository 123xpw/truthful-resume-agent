# Truthful Resume Agent：面试案例说明

这份文档不是“标准答案”，而是一组必须能被代码、测试或公开数据支撑的回答。不能把规划中的能力说成已经完成。

## 90 秒项目介绍

我在反复根据 JD 修改简历时发现，最大的风险不只是生成质量，而是模型容易把“相关”改写成“做过”，并且手工修改后很难追踪最终 PDF 中每条 bullet 的来源。因此我把系统拆成三层：LLM 只负责模糊理解和建议；确定性代码负责事实 ID、内容哈希、授权状态和最终产物审计；候选人保留最终授权权力。

工程上，项目包含 FastAPI、LangGraph 事实问答 Agent、关键词与 Qdrant 两条检索能力、本地 SQLite checkpoint、节点 trace、固定回归案例，以及实际 TeX/PDF 的来源和 SHA256 审计。需要区分的是：对话 Agent 的 `search_facts` 当前使用确定性 keyword top-5；Qdrant semantic 是独立 JD 匹配器中的 opt-in 路径，不在 Agent 工具内部。它目前是本地单用户 MVP，不具备多用户鉴权、高并发服务或生产 SLA。

## 1. 为什么需要 Agent，规则系统不能完成吗？

### 回答示例

能确定的事情我没有交给 Agent。例如事实是否存在、某段措辞是否仍有有效授权、最终 PDF 是否与登记哈希一致，都由确定性代码决定。Agent 只处理自然语言问题、检索意图和需要结合上下文解释的任务。

我采用的是“Agent 建议、代码约束、候选人授权”的分工，而不是让模型控制整个流程。即使模型跳过工具调用，检索节点也会由代码补做 `search_facts`；verifier 输出格式异常会 fail-closed；Agent 也没有写事实、授权或投递文件的工具。

### 可展示证据

- [`agent/graph.py`](../backend/resume_agent/agent/graph.py)：工具白名单、强制事实检索、严格 verifier 解析与反思路由。
- [`canonical.py`](../backend/resume_agent/canonical.py)：最终 TeX/PDF 的独立确定性审计。
- [`risk_policy.md`](risk_policy.md)：Agent 和候选人的权限边界。

## 2. Agent 真正创造了什么价值？

### 回答示例

当前已验证的价值是：用户可以用自然语言查询“哪些事实能够支撑某项经历”，Agent 会检索事实、引用 fact ID，并在回答后再次校验；不同 conversation ID 的状态相互隔离，运行重启后仍可恢复。

但我不会声称它已经提高了简历通过率。当前 Agent 是只读事实问答能力，核心交付流程仍以确定性 CLI 为主。短期不再扩建投递建议或通过率预测，而是保留一个可复现、可解释的本地工程原型。

### 可展示证据

- [`agent/runtime.py`](../backend/resume_agent/agent/runtime.py)：会话隔离、SQLite checkpoint、timeout 与节点 trace。
- [`test_runtime.py`](../backend/resume_agent/agent/test_runtime.py)：重启恢复、会话隔离、故障注入和 API 契约测试。

## 3. Prompt 注入让模型忽略事实库时怎么办？

### 回答示例

现有防线不是依赖一条 system prompt，而是限制模型能造成的后果：Agent 只有只读事实工具；未知工具会失败；模型跳过检索时代码强制补检索；回答必须由 evidence fact ID 支撑；verifier 异常或连续失败会阻断；最终简历授权和交付审计位于 Agent 之外。

目前仍有明确缺口：项目还没有独立的 Prompt 注入和间接注入攻击集，因此我只能说“缩小了攻击面并 fail-closed”，不能说“已经防住 Prompt 注入”。下一阶段要加入恶意 JD、恶意事实文本、伪造 tool output、越权写入请求和多轮诱导案例。

### 可展示证据

- [`agent/prompts.py`](../backend/resume_agent/agent/prompts.py)：事实引用约束。
- [`agent/graph.py`](../backend/resume_agent/agent/graph.py)：代码层工具和 evidence 门禁。
- [`evaluation_plan.md`](evaluation_plan.md)：现有评测边界和待补攻击案例。

## 4. 评测数据是谁标注的，能代表真实用户吗？

### 回答示例

当前评测主要是回归基线，不是生产 benchmark。匹配器使用 4 个公开 JD × 11 条脱敏事实的完整审计标签矩阵；检索集有 10 条固定 query；Agent 有 24 个固定场景，并在 CI 中用 fake provider 保证可复现。

公开 4-JD 基线用于干净克隆回归。另有一组私有 22 JD × 11 facts 的完整审计矩阵，明确标记为 `codex_audit_2026-08-27`，不是候选人 ground truth。两组数字不会合并宣传。它们适合发现代码回归和检索取舍，不能证明对所有岗位或用户泛化。

### 可展示证据

- [`matcher_labels.json`](../data/evaluation/matcher_labels.json)
- [`retrieval_cases.json`](../data/evaluation/retrieval_cases.json)
- [`agent_cases.json`](../data/evaluation/agent_cases.json)
- [`evaluation_plan.md`](evaluation_plan.md)

## 5. 为什么 SQLite 和进程锁可以支撑生产？

### 回答示例

它们不能支撑我所理解的多用户生产环境。当前定位是本地单用户工具：SQLite WAL 用于持久化，完整 Agent run 通过进程内锁串行化，以换取 checkpoint 和 trace 顺序稳定。这是有意识的范围选择，不是高并发方案。

如果业务进入多用户阶段，我会先用负载测试确认瓶颈，再拆成无状态 API、后台 worker、PostgreSQL checkpoint、Qdrant server 和队列，并增加鉴权、限流、指标与分布式 trace。在完成这些验证前，我不会在简历中写“高并发”或“生产级”。

### 可展示证据

- [`agent/runtime.py`](../backend/resume_agent/agent/runtime.py)：本地 SQLite 与 `_run_lock`。
- [`README.zh-CN.md`](../README.zh-CN.md#已知边界)：公开成熟度声明。

## 6. 项目节省了多少时间、提高了多少筛选效果？

### 回答示例

目前没有足够的数据支持“提高通过率”的结论。系统已经开始接入真实投递台账，但投递记录、JD、授权素材、最终 PDF 哈希和后续结果尚未全部关联，因此现在只能报告系统指标，不能做因果归因。

下一阶段需要记录四类指标：从 JD 到首份草稿的时间、候选人手工修改次数、交付前被门禁阻断的问题数、不同简历版本对应的筛选/笔试/面试结果。即使收集完成，也只能先做观察性分析，不能把相关性表述成因果提升。

### 当前可公开指标

| 指标 | 当前值 | 边界 |
| --- | ---: | --- |
| 匹配器审计集 | 4 JD × 11 facts | 小型人工标签矩阵 |
| Agent 固定案例 | 24 | fake provider，用于回归 |
| 检索固定 query | 10 | 不代表生产分布 |
| Qdrant Recall@5 | 1.00 | 仅当前脱敏数据集 |
| Qdrant MRR | 0.80 | 仅当前脱敏数据集 |

## 7. 为什么不每次直接把整份事实库放进 prompt？

### 回答示例

我最初假设检索能够减少 Token 和无关上下文，但当前只有 11 条事实，所以后来补了完整上下文基线。在 5 份不同方向 JD 的单次对照中，完整上下文的有用召回是 93.5%，keyword top-5 是 45.2%，有用精度分别是 70.7% 和 70.0%。

这说明小事实库阶段，完整上下文是更强的基线。但它的提示长度约翻倍，延迟也更高，且模型会倾向多选边缘素材。所以结论不是“RAG 没用”，而是应该先建简单基线，再根据事实数量、权限和成本证明检索复杂度。

### 可展示证据

- [`context_strategy_eval.py`](../backend/resume_agent/context_strategy_eval.py)：两种上下文策略的同约束对照。
- [`evaluation_plan.md`](evaluation_plan.md#small-bank-context-strategy-decision)：聚合结果和实验边界。

## 建议的现场演示顺序

先运行可复现的离线控制路径演示：

```bash
.venv/bin/python -m backend.resume_agent.agent_demo
```

它使用 scripted LLM，不需要 API Key，只证明编排、fail-closed 和 trace，不证明模型质量。然后再根据现场时间选择性展示：

1. 输入一份公开 JD，展示关键词匹配和不可写项。
2. 询问 Agent 一个有证据问题，再询问一个无证据或越权问题。
3. 打开 trace，展示实际经过的节点和 fact ID，不展示原始私密文本。
4. 展示候选人授权失效、未授权 bullet 被阻断的失败案例。
5. 展示最终 TeX/PDF 的 canonical audit 与 SHA256。
6. 最后主动说明 SQLite、数据规模、Prompt 注入评测和业务效果的当前边界。

主动展示失败路径比只展示一次成功生成更能说明工程判断。

# Truthful Resume Agent

[English](README.md) | [简体中文](README.zh-CN.md)

一个基于事实证据的本地 CLI，用于理解职位描述（JD）、召回相关经历，并在人工审核后生成简历草稿。

它不是一个通用的“把简历写得更厉害”的生成器，而是把三个问题分开处理：

1. 这份 JD 实际需要什么？
2. 已登记的经历中，哪些能够支撑这些要求？
3. 哪一种准确措辞是求职者愿意在面试中解释和承担风险的？

[查看脱敏后的 JD Insight 在线示例](https://123xpw.github.io/truthful-resume-agent/)

![脱敏后的 JD Insight 报告](docs/assets/jd-insight-demo.png)

## 能做什么

- 将公开 JD 拆分为岗位职责、硬性要求和加分项。
- 使用关键词匹配和可选的 Qdrant 向量检索召回结构化事实。
- 当事实库没有相关证据时，阻止把对应技术写入简历。
- 在确认前展示完整的核心版和保守版简历措辞。
- 在简历生成阶段拒绝待确认、手工伪造确认或已经过期的产物。
- 输出 Markdown/HTML 分析报告和 LaTeX 简历草稿。
- 用生成 PDF 的哈希记录真实投递结果，便于后续比较不同版本。

可选 LLM 只负责解释 JD、生成供人工审阅的面试问题或措辞候选。LLM 输出不能修改事实库、确认记录或最终简历。

## 五分钟快速开始

环境要求：Python 3.11 或更高版本。

```bash
git clone https://github.com/123xpw/truthful-resume-agent.git
cd truthful-resume-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

不配置 API Key，直接运行公开示例：

```bash
.venv/bin/python backend/run_cli.py validate
.venv/bin/python backend/run_cli.py analyze \
  --file data/sample_jds/alibaba_ai_agent_engineer.md \
  --name demo_alibaba
.venv/bin/python backend/run_cli.py explain-jd \
  --file data/sample_jds/alibaba_ai_agent_engineer.md \
  --no-llm --write
```

生成的 HTML 报告位于：

```text
data/outputs/alibaba_ai_agent_engineer/jd_insight.html
```

干净克隆会自动使用脱敏后的 `*.example.json`，这条演示路径不需要私人 profile 或 API Key。

## 完整简历流程

```bash
.venv/bin/python backend/run_cli.py prepare \
  --file data/sample_jds/tencent_ai_application.md \
  --name demo_tencent

.venv/bin/python backend/run_cli.py decide --name demo_tencent
.venv/bin/python backend/run_cli.py finalize --name demo_tencent
.venv/bin/python backend/run_cli.py status --name demo_tencent
```

在 `decide` 阶段，每条匹配经历都会显示两个完整版本：

- `A`：本次投递使用核心版。
- `B`：本次投递使用保守版。
- `C`：本次投递不使用这段经历。
- `D`：底层事实存在错误，需要先修正。

`decide` 要求在真实终端中运行，并拒绝普通管道输入。TTY 检查只是增加自动化绕过成本，不能从密码学上证明一定是真人输入。生成器还会检查待确认项、交互确认标记和过期产物。

编译生成的 TeX 需要 XeLaTeX、latexmk 或 Tectonic。只有 Tectonic 时，可执行 `finalize` 输出的编译命令。

## 使用自己的数据

将公开示例复制为只在本地使用的运行文件：

```bash
cp data/facts/facts.example.json data/facts/facts.json
cp data/resume_fragments/fragments.example.json data/resume_fragments/fragments.json
cp data/profile/profile.example.json data/profile/profile.private.json
```

然后编辑这些私人副本。它们已经被 `.gitignore` 排除。

每条事实应包含：

- 稳定的 `id`
- 事实摘要
- 检索关键词
- 明确的能力边界
- 风险等级

每个简历片段通过一个或多个 `source_fact_ids` 引用事实，并提供完整的 `A`/`B` bullet。编辑后运行：

```bash
.venv/bin/python backend/run_cli.py validate
```

## 可选 LLM 配置

LLM 客户端使用 OpenAI-compatible Chat Completions 接口。默认配置为 DeepSeek，也可以修改 URL 和模型。

```bash
cp .env.example .env
```

在 `.env` 中填写：

```dotenv
RESUME_AGENT_LLM_API_KEY=your-key
RESUME_AGENT_LLM_API_URL=https://api.deepseek.com/chat/completions
RESUME_AGENT_LLM_MODEL=deepseek-chat
```

程序不会把 API Key 写入生成报告，`.env` 也不会进入 Git。

## 架构

```text
公开 JD
   |
   +--> 确定性需求切分
   |
   +--> 关键词匹配
   |       |
   |       +--> 无事实依据技术的阻断检查
   |
   +--> 可选 fastembed + 嵌入式 Qdrant 候选召回
           |
           +--> 只作为人工审核候选

匹配到的 fact ID
   --> 与来源绑定的 A/B 简历片段
   --> 求职者在终端执行 A/B/C/D 确认
   --> 待确认、确认来源与产物时效检查
   --> LaTeX 简历草稿
```

最终简历生成路径是确定性的。JD Insight 中的实验性 LLM 措辞只供审核，`resume_generator.py` 没有读取这些内容的代码路径。

## 经过评测的检索，而不是只展示向量数据库

仓库提供了带人工标签的基线 `data/evaluation/matcher_labels.json`，以及生成的比较报告 `data/evaluation/matcher_report.md`。

当前结果是：在四份样例 JD 上，语义检索没有提升有效事实召回，并且在数据岗位样例中选出过一条无关事实。因此，Qdrant 只作为辅助召回路径，不会自动决定哪些经历进入简历。这个负面结果被保留在公开仓库中。

重新运行评测：

```bash
.venv/bin/python -m backend.resume_agent.eval_matchers
```

## 主要命令

| 命令 | 作用 |
| --- | --- |
| `validate` | 校验事实、简历片段、profile 和来源引用 |
| `analyze` | 匹配 JD 并生成确定性报告 |
| `explain-jd` | 生成可检查的 JD Insight Markdown/HTML |
| `prepare` | 保存 JD，并创建匹配报告和审核表 |
| `decide` | 在真实终端中审核完整 A/B 措辞 |
| `finalize` | 所有审核门通过后生成 TeX |
| `status` / `list` | 显示待确认、过期、草稿和可导出状态 |
| `gaps` / `expand-review` | 检查简历覆盖缺口，但不自动登记事实 |
| `record-outcome` | 记录实际投递状态和 PDF 哈希 |

运行 `python3 backend/run_cli.py --help` 查看全部参数。

## 隐私模型

公开仓库只包含脱敏示例和公开 JD。以下运行路径会被 Git 忽略：

- `data/facts/facts.json`
- `data/resume_fragments/fragments.json`
- `data/profile/profile.private.json`
- `data/jd_library/`
- `data/outputs/`
- `data/semantic_index/`
- `data/application_outcomes.json`
- `.env`

不要仅仅给现有私人仓库添加 `.gitignore` 就直接公开，因为旧提交仍可能包含已经删除的私人文件。这个公开仓库使用独立创建的干净 Git 历史。

## 验证

```bash
.venv/bin/python backend/run_cli.py validate
.venv/bin/python -m backend.resume_agent.smoke_test
.venv/bin/python -m backend.resume_agent.eval_matchers
```

Smoke test 覆盖待确认阻断、TTY 检查、EOF 恢复、复合事实、过期产物检测、语义候选隔离、无依据技术阻断、投递结果哈希和交付门禁。

设计细节与取舍记录在 `docs/technical_design.md`、`docs/risk_policy.md` 和 `docs/evaluation_plan.md`。

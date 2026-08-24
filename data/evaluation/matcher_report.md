# Matcher Evaluation

Compares the default keyword matcher with the opt-in semantic matcher.
This report is a review aid, not an automatic pass/fail judgment.
Relevance labels come from `codex_audit_2026-08-21` and are an auditable baseline, not candidate ground truth.

## Evaluation Inputs

- `facts` sha256: `634deb6d0334859ffa0940b0719fe74c87fe4c0594b99c222a11b666f236c6eb`
- `labels` sha256: `980f895fae6563dd044d433bb9adcdce7646a5937bf9e9e9f25595df5067e3aa`
- `jd:ai_agent_engineer.md` sha256: `5d15016f007c11d41eb0aea4833dc5509d0a9ad695e3abd19e8fd6527f516b0f`
- `jd:alibaba_ai_agent_engineer.md` sha256: `ea535b6026f638f086ca618dbb28ebaba343364519aaf3ab7a78c2a5f804506b`
- `jd:jd_data_application.md` sha256: `5f605f4ed11b67830d0f6a59d8af49d1792aab93746505d4c5494a1ae3c512e1`
- `jd:tencent_ai_application.md` sha256: `d608d6fe84f64dfafa1392d688905d9b676bc0a0eb2a12143fe0268f15406e0a`

## Audited Metrics

| JD | Matcher | Selected | Useful precision | Useful+marginal precision | Useful recall | Top-3 supported | Irrelevant selected | Missed useful |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| ai_agent_engineer.md | keyword | 8 | 62% | 100% | 71% | 100% | None | intern_csharp_ai_mvp, project_emotion_pixel_eval |
| ai_agent_engineer.md | semantic | 9 | 67% | 100% | 86% | 100% | None | project_emotion_pixel_eval |
| alibaba_ai_agent_engineer.md | keyword | 9 | 67% | 100% | 100% | 100% | None | None |
| alibaba_ai_agent_engineer.md | semantic | 9 | 67% | 100% | 100% | 100% | None | None |
| jd_data_application.md | keyword | 2 | 50% | 100% | 100% | 100% | None | None |
| jd_data_application.md | semantic | 4 | 25% | 75% | 100% | 67% | intern_csharp_ai_mvp | None |
| tencent_ai_application.md | keyword | 4 | 75% | 100% | 50% | 100% | None | intern_csharp_ai_mvp, project_truthful_resume_agent_agent, project_truthful_resume_agent_cli |
| tencent_ai_application.md | semantic | 6 | 67% | 100% | 67% | 100% | None | project_truthful_resume_agent_agent, project_truthful_resume_agent_cli |

### Macro Average Across JDs

| Matcher | Selected | Useful precision | Useful+marginal precision | Useful recall | Top-3 supported |
| --- | ---: | ---: | ---: | ---: | ---: |
| keyword | 23 | 64% | 100% | 80% | 100% |
| semantic | 28 | 56% | 94% | 88% | 92% |

## Summary Table

| JD | Job Type | Keyword Strong | Keyword Weak | Semantic Strong | Semantic Weak | Not Writable | Delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ai_agent_engineer.md | AI application / Agent engineering | intern_optimization_ai_coding (Claude Code, Codex, AI Coding) [useful]<br>project_truthful_resume_agent_cli (Python, fact bank, FastAPI) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful]<br>project_truthful_resume_agent_agent (Agent, tool calling) [useful] | intern_solver_integration_clarabel (AI Coding) [marginal]<br>intern_scip_heuristic_analysis (Claude Code) [marginal]<br>intern_data_automation (Python) [marginal]<br>project_chinese_learning_mvp (Agent) [useful] | intern_data_automation (semantic_score=0.530, matched_lines=1, Python) [marginal]<br>intern_optimization_ai_coding (Claude Code, Codex, AI Coding) [useful]<br>project_truthful_resume_agent_cli (Python, fact bank, FastAPI) [useful]<br>intern_csharp_ai_mvp (semantic_score=0.511, matched_lines=2) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful]<br>project_truthful_resume_agent_agent (Agent, tool calling) [useful] | intern_solver_integration_clarabel (AI Coding) [marginal]<br>intern_scip_heuristic_analysis (Claude Code) [marginal]<br>project_chinese_learning_mvp (Agent) [useful] | MCP, Redis, Docker | semantic_only: intern_csharp_ai_mvp |
| alibaba_ai_agent_engineer.md | AI application / Agent engineering | project_truthful_resume_agent_agent (LangChain, Agent, 记忆) [useful]<br>project_chinese_learning_mvp (Agent, Prompt Engineering) [useful] | intern_optimization_ai_coding (Claude Code) [marginal]<br>intern_scip_heuristic_analysis (Claude Code) [marginal]<br>intern_data_automation (Python) [marginal]<br>intern_csharp_ai_mvp (Prompt) [useful]<br>project_emotion_pixel_eval (Prompt Engineering) [useful]<br>project_truthful_resume_agent_cli (Python) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG) [useful] | project_truthful_resume_agent_agent (LangChain, Agent, 记忆) [useful]<br>project_chinese_learning_mvp (Agent, Prompt Engineering) [useful] | intern_csharp_ai_mvp (semantic_score=0.461, matched_lines=1, Prompt) [useful]<br>intern_optimization_ai_coding (Claude Code) [marginal]<br>intern_scip_heuristic_analysis (Claude Code) [marginal]<br>intern_data_automation (Python) [marginal]<br>project_emotion_pixel_eval (Prompt Engineering) [useful]<br>project_truthful_resume_agent_cli (Python) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG) [useful] | MCP, vLLM, Ollama, KV cache, SFT, RL | same fact set |
| jd_data_application.md | Data application / data engineering | intern_data_automation (Python, ETL, data processing) [useful] | project_truthful_resume_agent_cli (Python) [marginal] | intern_data_automation (semantic_score=0.500, matched_lines=2, Python, ETL, data processing) [useful] | project_dl_learning_lab (semantic_score=0.496, matched_lines=1) [marginal]<br>intern_csharp_ai_mvp (semantic_score=0.483, matched_lines=1) [irrelevant]<br>project_truthful_resume_agent_cli (Python) [marginal] | None | semantic_only: intern_csharp_ai_mvp, project_dl_learning_lab |
| tencent_ai_application.md | AI application / Agent engineering | project_chinese_learning_mvp (AI application, user-facing product features, intelligent dialogue, AI programming tools) [useful]<br>project_emotion_pixel_eval (model API, content generation, effect evaluation, evaluation) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database, Chunking) [useful]<br>intern_optimization_ai_coding (AI programming tools, requirement analysis) [marginal] | None | project_chinese_learning_mvp (AI application, user-facing product features, intelligent dialogue, AI programming tools) [useful]<br>project_emotion_pixel_eval (model API, content generation, effect evaluation, evaluation) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database, Chunking) [useful]<br>intern_optimization_ai_coding (AI programming tools, requirement analysis) [marginal] | intern_csharp_ai_mvp (semantic_score=0.486, matched_lines=1) [useful]<br>intern_solver_integration_clarabel (semantic_score=0.481, matched_lines=1) [marginal] | None | semantic_only: intern_csharp_ai_mvp, intern_solver_integration_clarabel |

## Review Questions

- Did semantic add a fact that is truly supported by the fact bank?
- Did semantic remove a useful keyword match?
- Did any not-writable item leak into a matched fact instead of staying blocked?
- Should semantic remain opt-in, become an auxiliary review section, or replace keyword for this JD type?

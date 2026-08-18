# Matcher Evaluation

Compares the default keyword matcher with the opt-in semantic matcher.
This report is a review aid, not an automatic pass/fail judgment.
Relevance labels come from `codex_audit_2026-08-15` and are an auditable baseline, not candidate ground truth.

## Audited Metrics

| JD | Matcher | Selected | Useful precision | Useful+marginal precision | Useful recall | Top-3 supported | Irrelevant selected | Missed useful |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| ai_agent_engineer.md | keyword | 6 | 80% | 100% | 67% | 100% | None | intern_csharp_ai_mvp, project_emotion_pixel_eval |
| ai_agent_engineer.md | semantic | 7 | 83% | 100% | 83% | 100% | None | project_emotion_pixel_eval |
| alibaba_ai_agent_engineer.md | keyword | 7 | 71% | 100% | 100% | 100% | None | None |
| alibaba_ai_agent_engineer.md | semantic | 7 | 71% | 100% | 100% | 100% | None | None |
| jd_data_application.md | keyword | 2 | 50% | 100% | 100% | 100% | None | None |
| jd_data_application.md | semantic | 5 | 20% | 60% | 100% | 67% | project_chinese_learning_mvp, intern_csharp_ai_mvp | None |
| tencent_ai_application.md | keyword | 4 | 75% | 100% | 60% | 100% | None | intern_csharp_ai_mvp, project_truthful_resume_agent_cli |
| tencent_ai_application.md | semantic | 5 | 80% | 100% | 80% | 100% | None | project_truthful_resume_agent_cli |

## Summary Table

| JD | Job Type | Keyword Strong | Keyword Weak | Semantic Strong | Semantic Weak | Not Writable | Delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ai_agent_engineer.md | AI application / Agent engineering | intern_optimization_ai_coding (Claude Code, Codex, AI Coding) [useful]<br>project_chinese_learning_mvp (Cursor, Agent) [useful]<br>project_truthful_resume_agent_cli (Python, fact bank) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful] | intern_solver_integration_clarabel (AI Coding) [unrated]<br>intern_data_automation (Python) [marginal] | project_chinese_learning_mvp (semantic_score=0.621, matched_lines=2, Cursor, Agent) [useful]<br>intern_data_automation (semantic_score=0.518, matched_lines=1, Python) [marginal]<br>intern_optimization_ai_coding (Claude Code, Codex, AI Coding) [useful]<br>intern_csharp_ai_mvp (semantic_score=0.511, matched_lines=2) [useful]<br>project_truthful_resume_agent_cli (Python, fact bank) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful] | intern_solver_integration_clarabel (AI Coding) [unrated] | MCP, Redis, Docker, FastAPI | semantic_only: intern_csharp_ai_mvp |
| alibaba_ai_agent_engineer.md | AI application / Agent engineering | project_chinese_learning_mvp (Cursor, Agent, Prompt) [useful] | intern_optimization_ai_coding (Claude Code) [marginal]<br>intern_data_automation (Python) [marginal]<br>intern_csharp_ai_mvp (Prompt) [useful]<br>project_emotion_pixel_eval (Prompt Engineering) [useful]<br>project_truthful_resume_agent_cli (Python) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG) [useful] | project_chinese_learning_mvp (semantic_score=0.564, matched_lines=3, Cursor, Agent, Prompt) [useful] | intern_csharp_ai_mvp (semantic_score=0.461, matched_lines=1, Prompt) [useful]<br>intern_optimization_ai_coding (Claude Code) [marginal]<br>intern_data_automation (Python) [marginal]<br>project_emotion_pixel_eval (Prompt Engineering) [useful]<br>project_truthful_resume_agent_cli (Python) [useful]<br>project_truthful_resume_agent_rag_qdrant (RAG) [useful] | LangChain, MCP, vLLM, Ollama, KV cache, SFT, RL | same fact set |
| jd_data_application.md | Data application / data engineering | intern_data_automation (Python, ETL, data processing) [useful] | project_truthful_resume_agent_cli (Python) [marginal] | intern_data_automation (semantic_score=0.479, matched_lines=2, Python, ETL, data processing) [useful]<br>project_chinese_learning_mvp (semantic_score=0.593, matched_lines=2) [irrelevant] | project_dl_learning_lab (semantic_score=0.496, matched_lines=1) [marginal]<br>intern_csharp_ai_mvp (semantic_score=0.483, matched_lines=1) [irrelevant]<br>project_truthful_resume_agent_cli (Python) [marginal] | None | semantic_only: intern_csharp_ai_mvp, project_chinese_learning_mvp, project_dl_learning_lab |
| tencent_ai_application.md | AI application / Agent engineering | project_chinese_learning_mvp (AI programming tools, AI application, user-facing, intelligent dialogue) [useful]<br>project_emotion_pixel_eval (model API, content generation, effect evaluation, evaluation) [useful]<br>intern_optimization_ai_coding (AI programming tools, requirement analysis) [marginal]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful] | None | project_chinese_learning_mvp (semantic_score=0.556, matched_lines=4, AI programming tools, AI application, user-facing, intelligent dialogue) [useful]<br>project_emotion_pixel_eval (model API, content generation, effect evaluation, evaluation) [useful]<br>intern_optimization_ai_coding (AI programming tools, requirement analysis) [marginal]<br>project_truthful_resume_agent_rag_qdrant (RAG, vector database) [useful] | intern_csharp_ai_mvp (semantic_score=0.486, matched_lines=1) [useful] | None | semantic_only: intern_csharp_ai_mvp |

## Review Questions

- Did semantic add a fact that is truly supported by the fact bank?
- Did semantic remove a useful keyword match?
- Did any not-writable item leak into a matched fact instead of staying blocked?
- Should semantic remain opt-in, become an auxiliary review section, or replace keyword for this JD type?

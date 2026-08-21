# Technical Design

## Design Goal

Build a JD-aware resume assistant that uses a user's private fact bank as the only source of truth. The system must make unsupported requirements visible instead of silently turning them into resume claims.

The technical design should support two usage modes:

- CLI or local web app for the private user.
- Desensitized demo mode for GitHub and interviews.

## Architecture

```text
User JD input
  -> JD memory writer
  -> JD parser
  -> requirement extractor
  -> hybrid retrieval over fact bank
  -> match and risk classifier
  -> resume strategy generator
  -> bullet draft generator
  -> manual review record
```

## Storage Strategy

The MVP should not start with a heavy database. It should use a layered storage model:

### Source Files

These files are the source of truth and remain human-editable:

```text
data/profile/
  profile.private.json        # private (gitignored)
  profile.example.json        # desensitized template

data/facts/
  facts.json                  # private (gitignored)
  facts.example.json          # desensitized template

data/resume_fragments/
  fragments.json              # private (gitignored)
  fragments.example.json      # desensitized template

data/jd_library/
  2026-08-12_tencent_ai_application.md   # private (gitignored)

data/agent_memory.json                   # long-term conversational memory (private, gitignored)

data/outputs/
  tencent_ai_application/
    match_report.md
    review_sheet.md
    selection_plan.md
    resume_draft.tex
    jd_insight.md
    jd_insight.html
    gap_report.md
    gap_warning.md
    gap_warning.html
```

### JSON Metadata

Application metadata (companies, job titles, job types, JD source path, decision status, generated output path, manual confirmation status) is stored as JSON/plain files under `data/outputs/<application>/`. There is no SQLite database; the MVP keeps everything in human-editable files.

### Vector Index

Qdrant (local) stores embeddings produced by fastembed for retrieval:

- fact chunks
- project chunks
- skill chunks
- JD requirement chunks

The vector index is derived data. It can be rebuilt from source files.

## JD Memory

Users should not need to manually create a Markdown file for every JD. The system should support:

```bash
resume-agent analyze --paste
resume-agent analyze --file jd/tencent.md
```

For pasted input, the system should:

1. Extract company and job title when possible.
2. Generate a stable file name.
3. Save the raw JD into `data/jd_library/`.
4. Save metadata to JSON under `data/outputs/<application>/`.
5. Run analysis on the saved JD.

The raw JD must be preserved. The parsed version is derived data.

## Fact Chunk Schema

Each retrievable fact chunk should contain:

```json
{
  "id": "intern_data_automation",
  "title": "数据自动化开发实习生",
  "source_file": "resume_content_bank.md",
  "source_section": "4.2 Guangzhou Jingyan Data",
  "summary": "Built a Python REST API based data automation workflow.",
  "keywords": ["Python", "REST API", "数据处理", "自动化任务", "阿里云"],
  "boundaries": [
    "No independent alerting system.",
    "No pagination; fixed-interval throttling plus 403 retry/token refresh."
  ],
  "risk": "medium"
}
```

## JD Requirement Schema

```json
{
  "id": "jd_req_004",
  "category": "hard_requirement",
  "text": "Understand RAG principles including parsing, chunking, vectorization, retrieval ranking.",
  "keywords": ["RAG", "chunking", "vectorization", "retrieval ranking"],
  "importance": "high"
}
```

## Match Schema

```json
{
  "requirement_id": "jd_req_004",
  "fact_ids": [],
  "match_level": "not_writable",
  "reason": "No fact-bank project shows RAG or vector database implementation.",
  "recommended_action": "Do not write RAG experience. Mark as study/project gap."
}
```

Match levels:

- `strong`: direct project or internship evidence.
- `weak`: related experience but incomplete implementation.
- `not_writable`: no supporting evidence.

## Retrieval

MVP retrieval should be hybrid:

1. Keyword retrieval for exact technologies and JD terms.
2. Vector retrieval for semantic similarity.
3. Rule-based filtering to prevent false positives.

Examples:

- `RAG` must not match generic `knowledge` or `document` unless fact chunks mention retrieval, chunking, embedding, or vector search.
- `production system` must not match demo MVP unless source fact explicitly supports deployment or real users.
- `model training` must not match model API calls or prompt engineering.

## Resume Wording Guardrails

Resume bullets come from a structured fragment registry written against
fact-bank records. Each fragment has a core A version and a conservative B
version; the candidate sees the exact wording before choosing one in an
interactive terminal. `explain-jd` may show experimental LLM wording
candidates, but they are not trusted inputs and the generator cannot consume
them.

Every fragment keeps internal trace metadata:

```json
{
  "fact_id": "intern_data_automation",
  "section": "实习经历",
  "entry_type": "internship",
  "date": "2025.10 - 2026.01",
  "title": "数据自动化开发实习生",
  "organization": "广州精研数据有限公司",
  "keywords": "Python, REST API, 数据处理, 自动化任务, 阿里云",
  "bullets": {
    "A": ["Built a Python REST API data workflow..."],
    "B": ["Implemented part of a Python data workflow..."]
  }
}
```

> 注：复合 fragment（如 `intern_optimization_combined`）额外带 `source_fact_ids` 字段列出其来源 fact；`project_url` 类型的 fragment 额外带 `url_text` / `url` 字段。`entry_type` 取值：`internship` / `project` / `project_url`。可选字段 `display_priority`（整数，越小越靠前）用于控制未在硬编码顺序中的 fragment 的展示位置。

## Dynamic Fragment Ordering

`layout_config.py` 维护实习与项目的展示顺序：

- 已知 fragment 按 `INTERNSHIP_ORDER` / `PROJECT_ORDER_BY_JOB_TYPE` 的硬编码顺序排列。
- 新增的 fragment（未出现在硬编码列表）按 `display_priority`（升序，未设则视为 999）+ `fact_id` 自动追加在末尾。
- 这样新增 fragment 无需改动 `layout_config.py` 即可被正确排序；若需控制其位置，在 fragment JSON 里设 `display_priority`。

LLM output cannot update facts, fragments, review decisions, or resume files.

## Output Reports

Each analysis run should produce:

- `match_report.md` — keyword + semantic match results with not-writable warnings
- `review_sheet.md` — A/B/C/D decision sheet for human-in-the-loop confirmation
- `selection_plan.md` — LLM-assisted fragment ranking (ID-restricted, capacity-bounded)
- `resume_draft.tex` — LaTeX resume draft (only after `finalize` passes all gates)
- `jd_insight.md` / `jd_insight.html` — Tier A (rule-based) + Tier B (LLM) JD analysis
- `gap_report.md` — unmatched JD requirements with gap classification
- `gap_warning.md` / `gap_warning.html` — prioritized gap warnings for interview prep

## Conversational Agent & Memory

A LangChain + LangGraph conversational agent (`backend/resume_agent/agent/`) sits alongside the deterministic CLI pipeline. It is a separate interaction surface; it does **not** replace the `prepare → decide → finalize → deliver` status machine and cannot write resume artifacts.

### Graph Topology

The state graph implements a retrieve → generate → verify → reflect loop:

```text
START
  -> retrieve   (binds search_facts / verify_fact tools)
  -> generate   (drafts an answer, injects long-term preferences)
  -> verify     (PASS / FAIL gate)
  -> conditional edge:
       PASS        -> END
       FAIL < 3 turns -> reflect -> retrieve  (retry)
       FAIL >= 3 turns -> END
```

`MAX_TURNS = 3` bounds the retry loop. The LLM is lazily constructed as a singleton via `get_model()` / `get_api_url()` / `get_api_key()` from `llm_client.py` (single source of truth for model/base URL).

### Two-Layer Memory

- **Short-term (in-process):** LangGraph `MemorySaver` checkpointer keyed by `thread_id` keeps multi-turn context within one chat session.
- **Long-term (cross-session):** `data/agent_memory.json` stores user preferences as key/value pairs via `memory.py`. `graph.py._generate` injects `list_preferences()` into the system prompt so the agent remembers cross-session preferences. CRUD is exposed through `save_preference` / `recall_preference` / `delete_preference` / `list_preferences`.

### Chat REPL Commands

`chat.py` exposes long-term memory operations directly in the REPL (no LLM round-trip):

- `prefs` — list all preferences
- `prefs <key>` — recall one preference
- `记住 <key>=<value>` — save a preference
- `忘记 <key>` — delete a preference

The LLM path is used only for general questions; it never updates facts, fragments, or confirmation records.

## Implementation Phases

### Phase 1: Documentation and Schemas

- Product spec
- Technical design
- Evaluation plan
- JSON schemas
- sample JD and sample profile

### Phase 2: CLI Prototype

- Paste or file JD input
- Save JD memory
- Rule-based JD parser
- Keyword retrieval
- Match report generation

### Phase 3: RAG Prototype

- Fact chunking
- Embedding index
- Hybrid retrieval
- Not-writable classifier

### Phase 4: Resume Drafting

- Bullet strategy generation
- Risk review
- LaTeX content draft
- Manual confirmation states

### Phase 5: Web App

- JD paste page
- Match matrix page
- Resume strategy page
- Version history page

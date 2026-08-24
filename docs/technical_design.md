# Technical Design

## Design Goal

Build a JD-aware resume assistant that uses a user's private fact bank as the only source of truth. The system must make unsupported requirements visible instead of silently turning them into resume claims.

The technical design should support two usage modes:

- CLI or local web app for the private user.
- Desensitized demo mode for GitHub and interviews.

The deterministic path stays local. Optional LLM paths are an explicit data
boundary: JD text, retrieved fact summaries/boundaries, chat content, or resume
text may be sent to the configured provider, and the current MVP does not
automatically redact those inputs.

## Architecture

The project evolved from a single-module CLI tool into a three-layer system: core decision layer, conversational agent layer, and web UI layer.

### Layered Module Map

```text
backend/resume_agent/
├── core decision layer (CLI pipeline)
│   ├── analyzer.py            # dual-matcher: keyword + semantic
│   ├── rules.py               # Fact + NOT_WRITABLE_TECH (evidence-driven)
│   ├── fact_store.py          # single source of truth for facts
│   ├── jd_insight.py          # Tier A (rule) + Tier B (LLM) + 4 guardrails
│   ├── resume_generator.py    # LaTeX generator (never imports LLM phrasing)
│   ├── selection.py           # deterministic or ID-restricted candidate ranking
│   ├── authorization_store.py # content-hash-bound reusable wording authorization
│   ├── layout_config.py       # dynamic fragment ordering (display_priority)
│   ├── fragments.py           # ResumeFragment schema + mtime-aware cache
│   ├── decision_flow.py       # interactive A/B/C/D confirmation
│   ├── aeo_review.py           # screening-model interpretation of actual JD + TeX
│   ├── canonical.py            # actual TeX/PDF bullet provenance + SHA256 audit
│   ├── outcomes.py             # observed application state + actual PDF hash
│   ├── delivery.py            # filename sanitization + artifact output
│   ├── cli.py                 # CLI entry: prepare → authorize → finalize → deliver
│   └── smoke_test.py          # end-to-end regression suite
│
├── semantic/ (retrieval subsystem)
│   ├── embedder.py            # fastembed multilingual MiniLM
│   ├── chunker.py             # fact → natural sentence (one chunk per fact)
│   ├── index.py                # Qdrant local COSINE collection + meta sidecar
│   ├── retriever.py            # query_points nearest-neighbor
│   ├── guardrails.py           # find_blocked_terms (evidence-driven blocklist)
│   ├── guarded_search.py       # dual-rail: blocked_terms + min_score floor
│   ├── thresholds.py           # shared score thresholds
│   ├── jd_eval.py              # evaluation framework (real JD runs)
│   └── keyword_baseline.py     # keyword matcher baseline for comparison
│
├── agent/ (LangGraph conversational layer)
│   ├── graph.py                # retrieve→generate→verify→reflect closed loop
│   ├── tools.py                # search_facts / verify_fact (simplified for LLM)
│   ├── prompts.py              # SYSTEM_PROMPT (truthfulness gate principle)
│   ├── memory.py               # save/recall/delete/list preferences (JSON)
│   ├── runtime.py              # SQLite checkpoint + bounded request runtime
│   ├── observability.py        # JSON logs + sanitized node traces
│   ├── test_runtime.py         # persistence/isolation/failure/API contracts
│   └── chat.py                 # REPL: prefs / 记住 / 忘记 commands
│
├── web/ (FastAPI UI layer)
│   ├── app.py                  # REST endpoints + static serving
│   └── templates/index.html    # 5-tab SPA (vanilla JS, no build step)
│
└── memory evolution (three-layer feedback loop)
    ├── interview_feedback.py   # record-interview: interview Q&A → boundary回写
    ├── gap_trends.py           # career-trends: cross-JD gap snapshot diff
    └── mastery_history.py      # mastery-history: C→B→A timeline tracking
```

### End-to-End Data Flow

```text
User JD input
  → CLI (prepare)
     → analyzer.py: keyword + semantic dual-matcher (merge_keyword_floor)
     → rules.py: find_not_writable (evidence-driven blocklist)
     → jd_insight.py: Tier A (structural) + Tier B (LLM advisory)
  → Human-in-the-loop (authorize; decide is a legacy alias)
     → A/B/C/D per-fact confirmation → confirmed_facts
  → CLI (finalize)
     → selection.py: build_selection_plan (capacity-bounded)
     → resume_generator.py: LaTeX from fragments (no LLM phrasing import)
  → CLI (deliver)
     → delivery.py: sanitized filename + artifacts
  → Actual artifact audit
     → aeo_review.py: advisory first-screen interpretation of JD + TeX
     → canonical.py: professional-bullet provenance + actual TeX/PDF SHA256

Conversational Agent (parallel surface, does not write resume):
  FastAPI/REPL → runtime.py → graph.py: retrieve → generate → verify → reflect
  runtime.py: conversation UUID → SQLite thread_id + sanitized trace_id
  memory.py: long-term JSON preferences injected at generate node
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
data/agent_runtime.sqlite3               # raw checkpoints + sanitized traces (private, gitignored)

data/evaluation/
  agent_cases.json                       # 24 deterministic graph scenarios
  retrieval_cases.json                   # keyword/Qdrant regression queries

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
- `aeo_review.md` / `aeo_review.html` — advisory AI-screening interpretation of the actual JD + TeX
- `canonical_audit.md` / `canonical_audit.json` — per-bullet provenance and actual artifact hashes
- `canonical_provenance.todo.json` — blocked manual bullets requiring candidate confirmation
- `canonical_delivery.json` — ready-only manifest for the exact TeX/PDF pair

## Conversational Agent & Memory

A LangChain + LangGraph conversational agent (`backend/resume_agent/agent/`) sits alongside the deterministic CLI pipeline. It is a separate interaction surface; it does **not** replace the `prepare → authorize → finalize → deliver` status machine and cannot write resume artifacts. `decide` remains a backward-compatible alias.

### Graph Topology

The state graph implements a retrieve → generate → verify → reflect loop. Retrieval tool output is retained as a full evidence bundle, candidate claims must cite an allowed `fact_id`, verifier responses use strict JSON, and malformed responses fail closed:

```text
START
  -> retrieve   (binds search_facts / verify_fact tools)
       dependency/tool error -> END (structured failure; no generation)
  -> generate   (drafts an answer, injects long-term preferences)
       LLM error -> END
  -> verify     (strict JSON PASS / FAIL against summary + boundaries)
  -> conditional edge:
       PASS        -> END
       FAIL < 3 turns -> reflect -> retrieve  (retry)
       FAIL >= 3 turns -> END
```

`MAX_TURNS = 3` bounds the verifier repair loop. Provider calls separately use
an explicit timeout and at most two retries for timeout, connection, 429, or
5xx failures. Authentication and invalid requests do not retry. The LLM is
lazily constructed via the configuration functions in `llm_client.py`.

### Two-Layer Memory

- **Short-term (persistent local API):** `SqliteSaver` is keyed by a UUID
  `thread_id`; it isolates conversations and survives process restarts. The
  REPL can still use the in-memory default when no runtime checkpointer is
  supplied. Checkpoints retain the messages and evidence required to resume
  the graph, so the database is private even though the separate trace tables
  are sanitized. SQLite is a local/lightweight backend, not a multi-user claim.
- **Long-term (cross-session):** `data/agent_memory.json` stores user preferences as key/value pairs via `memory.py`. `graph.py._generate` injects `list_preferences()` into the system prompt so the agent remembers cross-session preferences. CRUD is exposed through `save_preference` / `recall_preference` / `delete_preference` / `list_preferences`.

Conversation IDs provide state separation, not authentication. The API remains
single-user and does not expose long-term preference writes.

### Runtime Errors and Traces

`runtime.py` streams graph updates so every completed node is recorded with
duration, status, retry-safe error code, turn number, and evidence fact IDs.
Raw chat, JD, resume content, provider bodies, and API keys are excluded from
trace metadata. Each HTTP request receives an `X-Request-ID`; each Agent run
returns a `trace_id`.

- Fact-tool failures block before generation.
- Missing/rejected LLM configuration returns a structured 503.
- Provider timeouts return a structured 504 after bounded retries.
- Exhausted verifier repair is a domain-level `blocked` result, not a transport
  failure and not a verified answer.
- `/api/analyze` may explicitly fall back from semantic to keyword retrieval;
  the response reports `requested_matcher`, `used_matcher`, warnings, and
  `degraded=true`.

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

FastAPI-based web UI (`backend/resume_agent/web/`):

- `app.py` — REST endpoints for JD analysis, application status, gap trends,
  mastery history, interview feedback, Agent conversations/messages/traces,
  health/readiness, and static SPA serving.
- `templates/index.html` — 5-tab vanilla-JS diagnostics SPA (no build step): JD 分析 / 申请列表 / 缺口趋势 / Mastery 时间线 / 面试反馈.
- Start with `.venv/bin/uvicorn backend.resume_agent.web.app:app --reload`.
- Or run the public-example image with `docker compose up --build`.

The Web layer reuses core modules but does **not** mirror the CLI 1:1. Candidate authorization, finalization, AEO review, canonical registration, and delivery remain CLI-only in the current MVP.

## Memory Evolution: Three-Layer Feedback Loop

Beyond the conversational short-term / long-term memory, the project implements a three-layer feedback loop that lets the fact bank self-correct over multiple JD cycles:

### Layer 1: Interview Feedback → Fact Bank Self-Correction

`interview_feedback.py` + CLI `record-interview`:

- After each interview, the user records the questions asked and which facts were challenged.
- `--append-boundary <fact_id> <text>` writes new boundary clauses back into `facts.json`, so the fact bank learns from real interview exposure.
- `list-interview` reviews past records to prepare for similar roles.

### Layer 2: Cross-JD Gap Accumulation

`gap_trends.py` + CLI `career-trends`:

- Each `prepare` run snapshots the current not-writable technologies.
- `career-trends` compares the latest snapshot against the previous one, showing:
  - **new gaps** — technologies newly appearing in JDs but still unsupported.
  - **closed gaps** — technologies that were previously not-writable but now have fact-bank evidence.
- This turns one-off gap warnings into a trend signal for study prioritization.

### Layer 3: Mastery Timeline Tracking

`mastery_history.py` + CLI `mastery-history`:

- After each successful `authorize` (or legacy `decide`), the user's per-fact mastery level (A/B/C) is snapshotted; identical consecutive snapshots are deduplicated.
- `mastery-history` displays the C→B→A progression timeline per fact.
- This makes skill growth visible and motivates revisiting weak facts.

### Design Principle

The three layers share one principle: **the fact bank is a living artifact, not a static resume**. Interview feedback writes back boundaries, gap trends accumulate across JDs, and mastery progression tracks how facts improve over time. Together they close the loop between "apply → interview → learn → re-apply."

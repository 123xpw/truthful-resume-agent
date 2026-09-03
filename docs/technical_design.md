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
│   ├── job_analysis.py         # per-requirement deterministic evidence preview
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
│   └── templates/index.html    # single read-only application cockpit
│
└── memory evolution (three-layer feedback loop)
    ├── interview_feedback.py   # record-interview: interview Q&A → boundary回写
    ├── gap_trends.py           # career-trends: cross-JD gap snapshot diff
    └── mastery_history.py      # mastery-history: C→B→A timeline tracking
```

### End-to-End Data Flow

```text
User JD input
  → FastAPI preview (`/api/job-analysis/preview`)
     → job_analysis.py: explicit list extraction + per-item fact evidence
     → zero LLM calls, no JD persistence, fact_id/boundary citations
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

Resume-build metadata (job type, JD source path, decision state, generated output path, and manual confirmation state) remains in human-editable JSON/plain files under `data/outputs/<application>/`. Runtime state is separate: Agent checkpoints, observed outcome events, and optional Feishu snapshots use local SQLite databases. The files remain the source of truth for resume construction; SQLite supports runtime history and analysis.

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

The raw JD must be preserved after the user enters the saved application
workflow. The separate `/api/job-analysis/preview` endpoint is intentionally
non-persistent: it analyzes pasted text without creating a JD-memory file.

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

## Deterministic Preview Match Schema

```json
{
  "requirement_id": "jd_req_004",
  "kind": "hard_requirement",
  "jd_text": "Understand RAG and MCP.",
  "evidence_level": "not_writable",
  "evidence": [
    {
      "fact_id": "project_truthful_resume_agent_rag_qdrant",
      "support": "direct",
      "matched_keywords": ["RAG"],
      "boundaries": ["No MCP implementation."]
    }
  ],
  "blocked_claims": [
    {"term": "MCP", "source": "technology_guardrail"}
  ],
  "has_mixed_evidence": true
}
```

Preview evidence levels:

- `direct_support`: an exact supported aspect is present in a fact summary, or
  one fact matches at least two explicit keywords.
- `partial_support`: an exact keyword is present but the fact summary does not
  establish equivalent support.
- `no_evidence`: the current fact bank has no corresponding evidence.
- `not_writable`: an explicit technology or claim-strength boundary remains
  unsupported; mixed lines fail closed while retaining their supported facts.

The level applies to support found within one source list item. It is not a
claim that every clause in a compound requirement is satisfied.

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
       fewer than 3 reflections -> reflect -> retrieve  (retry)
       3 reflections exhausted -> END
```

`MAX_TURNS = 3` bounds the repair loop to three reflections after the initial
attempt, so the worst case contains four verifier attempts. Provider calls separately use
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
- The interactive page uses keyword retrieval and a 15-second client timeout.
  `/api/analyze` will not cold-download an embedding model: semantic requests
  fall back immediately unless `RESUME_AGENT_WEB_SEMANTIC_ENABLED=1` is set
  after local model preparation. Responses report `requested_matcher`,
  `used_matcher`, warnings, and `degraded=true`.
- `/api/meta` exposes a small API contract version. The page disables actions
  and requests a restart when current HTML is served by a stale backend.

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
  mastery history, interview feedback, local outcome CRUD/PDF selection,
  Agent conversations/messages/traces, health/readiness, and static SPA serving.
- `templates/index.html` — single-page vanilla-JS application cockpit (no build step): compact sync state, four current metrics, stage distribution, priority-by-stage bars, and five deterministic focus items. Legacy/experimental workflows are not shown in the daily UI.
- `templates/job_analysis.html` — separate deterministic JD evidence page. It
  calls only `/api/job-analysis/preview`, enforces the response safety contract,
  and renders exact JD items, fact IDs, boundaries, mixed evidence, and blocked
  claims without creating a saved application.
- `templates/project_review.html` — static project decision log and interview
  refresher. It reads no private runtime state and records the context-strategy
  result as a bounded design decision rather than a screening-outcome claim.
- `feishu_sync.py` — optional read-only Feishu Sheets client plus versioned SQLite snapshots; the first slice uses explicit manual sync and performs no remote writes.
- `feishu_analysis.py` — header-alias mapping plus deterministic dashboard semantics. `无合适岗位` is shelved rather than planning; unknown statuses stay visibly unmapped; focus ranking is assessment → interview → high-priority active → high-priority planning → missing role.
- `feishu_links.py` — local, content-bound links from a ledger sequence to an application workflow and optional PDF hash; remote rows are never mutated.
- Start with `.venv/bin/uvicorn backend.resume_agent.web.app:app --reload`.
- Or run the public-example image with `docker compose up --build`.

The Web layer reuses core modules but does **not** mirror the CLI 1:1. Candidate authorization, finalization, AEO review, canonical registration, and delivery remain CLI-only in the current MVP.

Outcome endpoints are deterministic and make no LLM calls. PDF references use
opaque `output:` or `delivery:` roots and resolve paths before hashing; traversal
outside those roots and non-PDF targets are rejected. The source of truth is a
versioned local SQLite database with WAL, busy timeout, prepared parameter
binding, active-date/application indexes, and an append-only mutation audit.
User deletion sets `archived_at`; restoration clears it and appends another
audit snapshot. Every mutation creates a verified rolling SQLite backup, and a
full restore first creates a safety backup. JSON/CSV export includes archived
history.

Feishu sync uses an app-scoped bearer token to discover the configured or first
visible worksheet and read a bounded cell range. It stores content hashes,
source revisions, timestamps, and normalized row snapshots in additive SQLite tables.
Trailing empty rows and columns from a bounded API range are removed without
changing any populated cell.
Unchanged content updates freshness without adding a duplicate snapshot. The
page renders the last successful snapshot first, then performs one background
sync and preserves the old dashboard on failure. The spreadsheet remains
authoritative; local outcome CRUD is retained as a compatibility/emergency API
but is not exposed in the daily cockpit.
The tenant token is cached only in process memory and refreshed ten minutes
before reported expiry. A rejected token invalidates the cache and permits one
forced refresh/retry; credentials and bearer tokens are never persisted.

Local application links use the ledger's unique sequence plus a hash of the
sequence/company/role/application-date identity. They also record the selected
PDF SHA256. A changed row, missing/replaced PDF, missing workflow, failed
canonical audit, or audit/PDF hash mismatch remains visible instead of being silently accepted. Unlinking
archives local metadata and never changes the remote spreadsheet.

Existing private `application_outcomes.json` records receive stable legacy IDs
and are imported once without mutating the source. Native runs default to
`data/application_tracker.sqlite3`; Compose sets `RESUME_AGENT_OUTCOME_PATH`
inside the existing runtime volume so events and backups survive container
replacement. `RESUME_AGENT_DATA_MODE` defaults to `preview`; `pilot` and
`trusted` are operational declarations, not inferred from event count.

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

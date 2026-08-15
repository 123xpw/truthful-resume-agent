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
data/private_profile/
  resume_content_bank.md
  fact_alignment.md
  match_matrix.md

data/jd_library/
  2026-08-12_tencent_ai_application.md
  2026-08-12_jd_data_application.md

data/outputs/
  tencent_ai_application/
    match_report.md
    resume_strategy.md
    risk_review.md
    resume_content.tex
```

### SQLite Metadata

SQLite stores structured metadata for filtering and history:

- companies
- job_titles
- job_types
- JD source path
- creation time
- decision status
- generated output path
- manual confirmation status

### Vector Index

Chroma or FAISS stores embeddings for retrieval:

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
4. Create metadata in SQLite.
5. Run analysis on the saved JD.

The raw JD must be preserved. The parsed version is derived data.

## Fact Chunk Schema

Each retrievable fact chunk should contain:

```json
{
  "id": "internship_jingyan_api_001",
  "source_file": "resume_content_bank.md",
  "source_section": "4.2 Guangzhou Jingyan Data",
  "fact_type": "internship",
  "tags": ["Python", "REST API", "Excel automation", "ETL"],
  "claim": "Built a Python REST API based data automation workflow.",
  "evidence": "Retrieved selected index data, computed high/low average, wrote multi-sheet Excel reports.",
  "boundary": "No independent alerting system; no pagination/rate-limit handling.",
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
  "bullets": {"A": ["Built a Python REST API data workflow..."], "B": ["Implemented part of a Python data workflow..."]},
  "source_fact_ids": ["internship_jingyan_api_001"],
  "risk": "medium",
  "manual_status": "pending"
}
```

LLM output cannot update facts, fragments, review decisions, or resume files.

## Output Reports

Each analysis run should produce:

- `match_report.md`
- `resume_strategy.md`
- `risk_review.md`
- optional `resume_content.tex`

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

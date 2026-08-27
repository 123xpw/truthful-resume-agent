# Risk Policy

## Principle

The system must prioritize truthfulness over keyword matching.

## Match Levels

### Strong Match

The fact bank contains direct project or internship evidence for the JD requirement.

Example:

- JD requires Prompt Engineering.
- Fact bank contains a project using prompts to constrain JSON output or generate visual conditions.

### Weak Match

The fact bank contains related experience, but not a full implementation of the JD requirement.

Example:

- JD requires Agent workflow.
- Fact bank contains Coze-based demo configuration, but not a custom Agent framework.

### Not Writable

The JD requirement has no supporting fact-bank evidence. It must not appear in the resume.

Example:

- JD requires Kubernetes operations.
- Fact bank has no Kubernetes deployment evidence.
- The system must mark Kubernetes as not writable.

## Risk Levels

### Low

The user can explain the work independently and has clear artifacts.

### Medium

The work is real, but the user should prepare details before interview.

### High

The wording may invite deep technical questions that exceed current mastery. Use reduced wording or remove.

## Generation Rules

- Do not add tools, frameworks, metrics, or achievements absent from the fact bank.
- Do not convert a demo into a production system.
- Do not convert platform configuration into custom framework development.
- Do not write "optimized accuracy" or "improved performance" without measured evidence.
- Do not write any JD technology—including RAG, LangChain, MCP, Redis, Docker,
  Kubernetes, or vector database—unless the current fact bank supports the
  exact requested claim. This is an evidence check, not a permanent denylist.

## Manual Confirmation States

- A: the displayed core fragment is accurate and explainable; it may enter the selection pool.
- B: only the displayed conservative fragment is accurate and explainable; it may enter the selection pool.
- C: the fact is broadly accurate, but the candidate does not currently accept its interview follow-up risk.
- D: the fact record itself appears wrong and must be corrected before use.

These states set factual and interview-risk eligibility, not final editorial
selection. The selection planner chooses among A/B items for the target JD and
page capacity and must report every omitted eligible item. The states do not
prove whether an event happened; the candidate-confirmed fact/profile inputs
remain the trust boundary.

## Optional LLM Data Boundary

Enabling an LLM can transmit JD text, retrieved fact summaries and boundaries,
chat content, or resume text to the configured provider. The MVP does not
automatically redact those inputs. Use `--no-llm` and deterministic selection
when data must stay fully local. Provider output is advisory and cannot update
facts, authorizations, provenance confirmations, or delivered artifacts.

## Agent Service Failure Boundary

- Conversation IDs isolate checkpoint state but are not authentication.
- Fact-store/tool failure must block generation; empty evidence is not a
  substitute for an available evidence source.
- Semantic retrieval may fall back to deterministic keyword retrieval only
  when the response explicitly reports degraded operation.
- Only transient LLM failures may retry, with a bounded attempt count.
- Raw chat, JD, resume text, provider bodies, and API keys must not be stored in
  structured trace metadata.
- SQLite checkpoints necessarily retain conversation state and evidence for
  resume-after-restart behavior; the runtime database is private and must not
  be committed or treated as a sanitized trace export.
- A verifier-exhausted draft is `blocked`, not verified, and remains outside
  authorization and delivery paths.

## Local Outcome Tracker Boundary

- Recording, editing, listing, summarizing, archiving, and restoring outcomes must not call
  an LLM or transmit the private event data externally.
- A Web-selected resume may be hashed only when it resolves to an existing PDF
  under the project output root or the configured delivery root.
- Filename warnings such as `未验证勿投递` and `废弃` remain visible; the
  tracker records observed history but does not certify that a file was safe to
  submit.
- Feishu synchronization starts read-only. App credentials and spreadsheet
  tokens stay in ignored local configuration, are never returned by the API or
  persisted in SQLite, and must not be included in public logs or examples.
- A missing Feishu row never deletes local JD, resume, authorization, or audit
  evidence. Remote write-back requires a separate policy and is out of scope for
  the first synchronization slice.
- Feishu-to-application links are local metadata bound to a row identity hash
  and optional PDF SHA256. A changed row or artifact is reported as stale;
  unlinking archives local metadata and never deletes the remote row or resume.
- The default `preview` mode is not an authorization to discard the user's
  independent source records. Promotion to `pilot` or `trusted` is a deliberate
  operational decision after migration, restart, concurrency, backup, restore,
  export, and privacy checks.
- SQLite databases, WAL/SHM files, rolling backups, legacy JSON, and launcher
  logs/PIDs are private runtime artifacts and must remain outside Git and image
  build contexts.

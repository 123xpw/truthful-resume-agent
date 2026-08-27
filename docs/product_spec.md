# Product Spec

## Problem

Candidates often use LLMs to tailor resumes for different JDs, but generic generation easily creates unsupported claims. This creates interview risk and damages trust.

The system should answer a stricter question:

> Given my real fact bank, what can I honestly write for this JD, and what must I prepare before applying?

Within candidate-confirmed evidence, the product should maximize visible JD
relevance, specificity, and persuasive value. Truthfulness is a constraint,
not a substitute for competitive resume quality.

The public engineering narrative centers on evidence control and the read-only
Agent. The Feishu-backed application cockpit and deterministic JD preview are
optional personal recruiting-operations extensions that share the local data
boundary; they are not evidence that the Agent improves screening outcomes.

## Target Users

- Students preparing internship or campus recruitment applications.
- Candidates who want JD-specific resumes without fabricating experience.
- Candidates who need interview-risk-aware resume tailoring.

## Core Workflow

1. User maintains a fact bank.
2. User provides a target JD.
3. System parses JD requirements.
4. System retrieves relevant fact-bank entries.
5. System builds a match matrix.
6. System identifies unsupported requirements.
7. System loads source-linked A/B fragments; optional LLM wording remains advisory and outside the generator.
8. User authorizes the exact factual wording or vetoes its interview risk.
9. System ranks only eligible source-linked fragments against the JD and page capacity, then reports included and omitted items.
10. System audits the actual TeX/PDF, stores bullet provenance and hashes, and records observed outcomes separately.
11. When configured, the Web UI reads the candidate's Feishu spreadsheet as the operational application ledger and stores versioned local snapshots without giving the LLM write access.
12. A candidate may locally bind a stable ledger sequence to a known application and PDF; row/PDF changes invalidate that binding without changing Feishu.

## Core Pages

### Application Cockpit

Optional personal extension. Uses the read-only Feishu ledger to answer three questions without reproducing
the source table: current pipeline shape, where high-priority applications are
concentrated, and which five records deserve attention today. Sync state is a
compact header, not a separate product module. Unknown statuses remain visible
instead of being guessed, and the first version does not claim conversion or
stage-stagnation metrics without sufficient history.

### Fact Bank

Stores facts about education, internships, projects, skills, evidence, and risk boundaries.

### JD Analyzer

Parses the JD into:

- job type
- core responsibilities
- hard requirements
- bonus requirements
- likely interview topics

The deterministic preview treats each explicit JD list item as one analysis
unit. It preserves the source text and section, cites every matched fact by
`fact_id`, exposes fact boundaries, and marks unsupported technology or claim
strength separately. Previewing neither saves the pasted JD nor calls an LLM.
Because one list item may contain several capabilities, a direct evidence hit
does not mean the candidate fully satisfies the entire item.

### Project Decision Review

A static, privacy-safe page records the project's real origin, framework
trade-offs, context-strategy experiment, engineering lessons, current scope,
and interview wording. It does not read runtime data or call an LLM.

### Match Matrix

Table columns:

- JD requirement
- retrieved fact
- evidence source
- match level
- risk level
- recommended action

### Resume Draft

Builds the draft only from authorized source-linked fragments. Hand-edited final bullets require a separate candidate-confirmed provenance mapping before canonical registration.

### Interview Risk

Lists likely follow-up questions and preparation notes for high-density bullet points.

### Version History

Tracks company, job title, JD, generated draft, manual edits, and final decision.

## Success Criteria

- The system refuses to write unsupported technologies.
- Every generated bullet has a fact source.
- Editorial selection is performed by the system rather than delegated to the candidate, and every omitted eligible item has a visible reason.
- The user can see why a JD requirement is matched or not matched.
- The deterministic JD preview is reproducible, makes zero LLM calls, writes no
  JD memory, and never presents a positive match without a cited `fact_id`.
- The output includes interview risks, not only resume wording.
- The demo can be explained in two minutes.
- Matching quality is measured on an attributed label set, including missed
  useful facts and irrelevant selections.
- Application outcomes are linked to the exact resume hash without claiming
  unsupported causality.
- A clean public clone passes validation and smoke tests using only desensitized
  `*.example.json` data.
- The read-only Agent API isolates UUID conversations, persists local
  checkpoints, exposes sanitized node traces, and never gains fact,
  authorization, or delivery write access.
- Dependency failures produce bounded retries, explicit degradation, or a
  fail-closed response rather than an unverified answer.
- Legacy local outcome APIs record, edit, archive, and restore events without an
  LLM call, but remain outside the daily cockpit while Feishu is authoritative.
- Outcome persistence uses a local versioned SQLite schema. Mutations are
  audited and backed up; user deletion is a reversible archive operation.
- The cockpit renders the last successful snapshot before background sync and
  keeps that view available when the remote read fails.
- Feishu synchronization is read-only by default, exposes freshness and source
  revision, deduplicates unchanged snapshots, and never stores the App Secret in SQLite.

# Evaluation Plan

## Purpose

This project should be evaluated by whether it improves truthful job application decisions, not by whether it produces fluent resume text.

## Evaluation Dimensions

### 1. Truthfulness

Question: Does every generated claim trace back to the fact bank?

Metrics:

- Source coverage: percentage of bullets with at least one source fact.
- Unsupported claim count: number of tools, metrics, or achievements absent from the fact bank.
- Boundary violation count: number of bullets that overstate demo, team, or AI-assisted work.

Target for MVP:

- 100 percent source coverage.
- Zero unsupported technologies in final draft.
- Zero untraced professional bullets in a registered canonical TeX/PDF pair.

### 2. JD Matching Quality

Question: Does the system select facts that actually support the target JD?

Metrics:

- Human rating for each selected fact: useful / marginal / irrelevant.
- Top-3 fact precision.
- Whether the recommended resume section order matches human judgment.

Target for MVP:

- At least 80 percent of selected facts are useful or marginal.
- No irrelevant fact should be placed in the first section.

Current reproducible baseline uses a complete 4-JD x 11-fact label matrix:

- Keyword macro useful recall / precision: 80% / 64%.
- Semantic macro useful recall / precision: 88% / 56%.
- Semantic retrieval remains opt-in because the data-role sample contains an
  irrelevant selection.

The maintainer also keeps a private, frozen target-domain v2 audit outside Git:

- 22 unique target JDs frozen on 2026-08-27 across 10 role families.
- 11 current private facts and 242 complete JD/fact relevance labels.
- Keyword macro useful precision / recall: 68% / 71%; zero explicitly
  irrelevant selections and two zero-result JDs.
- Semantic macro useful precision / recall: 67% / 76%; four explicitly
  irrelevant selections and one zero-result JD.

This local result supports semantic as an opt-in recall aid rather than the
default selector. It is an attributed engineering regression set, not candidate
ground truth or a production benchmark. The private cohort manifest, labels,
report, and source JDs remain Git-ignored; the public 4-JD baseline stays the
clean-clone reproducibility contract. Old and v2 metrics must never be combined
under one sample-size claim.

#### Small-bank context strategy decision

A separate private single-run experiment asks whether retrieval is justified
at the current scale rather than assuming it is. Five diverse target JDs were
run once with the same model and output contract under two evidence modes:

- all 11 structured facts, including summaries and boundaries;
- the conversational Agent's actual deterministic keyword top-5 bundle.

Full context selected 29 of 31 useful labels (93.5%) and keyword top-5 selected
14 (45.2%). Useful precision was 70.7% versus 70.0%; useful-or-marginal
precision was 100% versus 95%. Full context averaged 14.5k prompt characters
and 6.2 seconds, versus 7.4k characters and 3.8 seconds for keyword in that
run. Character counts are not token counts, and latency is environment-specific.

The decision is to treat full context as the stronger baseline while the bank
contains only 11 facts. It does not authorize unrestricted model selection:
full context selected substantially more marginal material and sometimes
overstated relevance strength. Private JDs, facts, generated claims, hashes,
and the raw report remain ignored under `context_strategy_*`. This experiment
is a design aid, not a repeated statistical benchmark or screening-outcome
claim.

### 3. Risk Control

Question: Does the system identify interview risks before resume submission?

Metrics:

- High-risk bullet recall: percentage of human-identified risky bullets also flagged by the system.
- Not-writable keyword recall: percentage of unsupported JD keywords correctly flagged.
- False-safe count: unsupported requirement marked as safe.

Target for MVP:

- Zero false-safe on explicitly unsupported technologies such as Kubernetes,
  Redis, RocketMQ, MCP, or Docker unless current fact-bank evidence exists.

### 4. Usefulness

Question: Does the system reduce manual resume tailoring effort?

Metrics:

- Time to first strategy report.
- Number of manual corrections needed after report.
- User confidence rating before and after analysis.

Target for MVP:

- Produce a first strategy report within one minute for a pasted JD.
- Reduce repeated manual review steps by saving JD memory and past decisions.

### 5. Interview Defensibility and Screening Interpretation

Question: Can the user answer likely follow-up questions from generated bullets?

Metrics:

- Candidate can authorize core/conservative wording or veto the fact with A/B/C/D.
- AEO review reports likely screening persona, red flags, business-problem fit,
  and possible misreadings over the actual JD and TeX source.
- Interview feedback can be attached to a known fact and optionally appended as
  a new boundary.

### 6. Public Reproducibility

Question: Does the repository work without the private runtime files used by
the maintainer?

Metrics:

- `validate`, the smoke suite, Agent tests, matcher evaluation, and retrieval
  sanity check pass in a clean clone containing only `*.example.json` data.
- CI uses no private profile, facts, fragments, JD library, or API key.

### 7. Agent Runtime Reliability

Question: Does the service preserve boundaries when state or dependencies fail?

Metrics:

- Conversation isolation across distinct UUID/thread IDs.
- Checkpoint recovery after a runtime restart.
- Bounded retry count for transient provider errors.
- Fail-closed behavior when fact tools are unavailable.
- Complete node sequence in the sanitized trace.
- Raw-message leakage count in trace storage: zero.

The deterministic Agent regression set contains 24 fixed scenarios covering
supported facts, unsupported queries, one-pass and repair-pass verification,
malformed verifier output, and retry exhaustion. Provider behavior is faked in
CI so this suite is reproducible and does not consume an API key.

### 8. Retrieval Regression

The fixed retrieval set runs the same 10 queries through:

- `keyword_search` as the conservative baseline.
- the actual embedded-Qdrant `semantic_search` / `query_points` path.

Current public-example baseline:

- Keyword Recall@5 / MRR: 0.50 / 0.50.
- Qdrant semantic Recall@5 / MRR: 1.00 / 0.80.

CI fails if Qdrant semantic Recall@5 drops below 0.90 or MRR drops below 0.60.
This small set detects regressions; it is not a production benchmark.

## Test Set

The audited matcher set uses the four public sample files:

- `ai_agent_engineer.md`
- `alibaba_ai_agent_engineer.md`
- `jd_data_application.md`
- `tencent_ai_application.md`

For each JD, store:

- Raw JD
- Human decision
- Expected strong facts
- Expected weak facts
- Expected not-writable requirements

## Manual Review Checklist

For every generated bullet:

1. Is this fact in the content bank?
2. Does it match the JD requirement?
3. Is it too broad or overclaimed?
4. Can the user explain it in two minutes?
5. Does it create unnecessary interview risk?

## Failure Modes

The system fails if it:

- Writes a technology the user has not used.
- Converts a demo into a production system.
- Treats AI-assisted generation as independent implementation.
- Ignores obvious JD gaps.
- Produces a resume without risk review.
- Registers a hand-edited canonical resume whose professional bullets have no
  current authorization or candidate-confirmed provenance.
- Passes locally only because private ignored files happen to exist.
- Shares short-term messages between different conversation IDs.
- Continues generation after the fact tool fails.
- Silently falls back from semantic retrieval without a degraded marker.
- Stores raw private message, JD, or resume text in structured trace metadata.

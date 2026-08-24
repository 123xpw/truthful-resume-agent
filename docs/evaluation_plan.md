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

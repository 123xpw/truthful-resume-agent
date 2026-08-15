# Project Reset — Product Redefinition

Author: candidate, recorded by Claude on request. Date: 2026-08-14.

This is a locked-in redefinition, not a proposal. It supersedes any prior
discussion (in `docs/codex_action_items.md` or `docs/claude_sync.md`) that
implied the goal was "add RAG generation to the resume." Codex and Claude
should treat this file as the source of truth for scope; if a future
instruction seems to contradict it, flag the contradiction instead of
quietly following the newer instruction.

This is a sharpening of the original intent, not a new direction. The
implementation had drifted by treating citation checks and a second LLM
judgment as proof that generated wording was fully supported. They are not
proof, so trusted resume wording stays deterministic and source-linked.

## What this product is

Truthful Resume Agent is not a resume generator, and not a tool that uses
an LLM to dress up experience. Its job:

Given a JD, help the candidate understand what the role actually requires,
retrieve the candidate's real experience, judge what can be written, what
can only be written cautiously, and what must not be written at all — and
produce interview-risk and preparation output.

## Where an LLM is allowed to help

1. **Decompose the JD** — responsibilities, hard requirements, implicit
   requirements, bonus points, likely follow-up questions.
2. **Translate the JD** — turn abstract requirements into concrete
   engineering capabilities a candidate can self-assess against.
3. **Generate interview follow-ups** — if a given confirmed experience were
   discussed, what might an interviewer ask about it. These are questions,
   not candidate claims or suggested answers.
4. **Show experimental wording candidates** — only as visibly untrusted review
   material. They cannot be consumed by resume generation.

## Where an LLM is not allowed to help

1. Does not invent experience from nothing.
2. Does not decide whether the candidate actually did something.
3. Does not write unconfirmed content into `facts.json`, `fragments.json`,
   or any resume file.
4. Does not force-fit RAG, vector database, "Agent," or any other term
   into content just because a JD mentions it.

## The precise version of "LLM has no role here"

That statement, as previously used in this project, is too blunt. Correct
version:

> An LLM has no decisive role in proving what the candidate actually did.
> An LLM does have a role in understanding a JD, decomposing its
> requirements, and simulating an interviewer.

So: do not build "LLM generates the final resume." Build "LLM explains the
JD while deterministic retrieval, source-linked fragments, and candidate
confirmation control the resume." Whether something can ultimately be
written stays a decision made by the fact bank plus the candidate's own
confirmation.

## Guardrail split (added by Claude, not just a restatement)

The four allowed uses above are not uniformly low-risk. Split them before
implementing:

- **JD-only analysis** (items 1–2 above): operates only on JD text, which
  is given, public, and self-checkable against the source. No claim about
  the candidate is made. Lowest risk — no special guardrail needed beyond
  "show the source JD alongside the output so it's easy to spot-check."
- **Candidate-fact-touching output** (items 3-4 above): unknown `fact_id`
  values and unsupported output shapes are dropped. Experimental wording also
  passes a separate boundary-risk screen, but neither check proves factual
  entailment. Such output cannot enter resume generation.

## Update, 2026-08-15: do not force the RAG label

The project has real embedding retrieval and a local Qdrant index. Current
evaluation shows that semantic retrieval can broaden recall, but it has not
beaten the keyword baseline and produced one irrelevant data-role match.
It therefore remains an auxiliary candidate-retrieval path.

An earlier implementation called LLM-generated phrasing plus a citation check
and a second LLM boundary check "verified RAG." That description was too
strong. A real `fact_id` proves where retrieval started; it does not prove
that every detail in free-form output is entailed by that fact. Asking another
model to judge the sentence raises the bar but is not a code-level guarantee.
The phrasing feature remains only as an explicitly experimental, review-only
demonstration; it is outside the trusted resume path. The honest description
is a local vector/embedding retrieval workflow with an auxiliary bounded RAG
experiment, not a production-grade or provably grounded RAG resume generator.

## Immediate priorities

1. Keep unconfirmed content out of resume artifacts and mark any older
   preview files as unconfirmed in CLI status.
2. Evaluate matching quality against an attributed, reviewable label set;
   semantic retrieval remains candidate recall unless it beats the baseline.
3. Show the exact A/B resume fragments before the candidate chooses one,
   and never repeat already-completed decisions when a session resumes.
4. Keep private facts, fragments, profiles, outputs, indexes, and outcome
   history outside Git; public clones use desensitized example data.
5. Record observed application outcomes against resume hashes. Do not claim
   that a wording change caused an outcome without enough evidence.

## Relationship to existing docs

- `docs/risk_policy.md`'s invariants (truthfulness over keyword matching,
  boundaries, not-writable list) are unchanged and still govern everything
  `explain-jd` touches.
- `docs/codex_action_items.md` keeps tracking day-to-day handoff items;
  this file is the higher-level scope anchor those items should not
  contradict.

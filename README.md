# Truthful Resume Agent

[English](README.md) | [简体中文](README.zh-CN.md)

An evidence-grounded CLI for understanding a job description, retrieving
relevant experience, and producing a review-gated resume draft.

It is not a generic "make my resume sound stronger" generator. The system
separates three questions:

1. What does this JD require?
2. Which recorded experiences support those requirements?
3. Which exact wording is the candidate willing to defend in an interview?

[Live desensitized JD Insight demo](https://123xpw.github.io/truthful-resume-agent/)

![Desensitized JD Insight report](docs/assets/jd-insight-demo.png)

## What It Does

- Parses public JD text into responsibilities, hard requirements, and bonus items.
- Matches a structured fact bank with keyword search and optional Qdrant vector retrieval.
- Blocks unsupported technologies until the fact bank contains evidence for them.
- Shows the exact core and conservative resume wording before confirmation.
- Rejects pending or hand-edited decisions during resume generation.
- Produces Markdown/HTML reports and a LaTeX resume draft.
- Records observed application outcomes against the generated PDF hash.

The optional LLM path helps explain the JD and generate review-only interview
questions or wording candidates. LLM output cannot update facts, confirmation
records, or resume files.

## Five-Minute Start

Requirements: Python 3.11+.

```bash
git clone https://github.com/123xpw/truthful-resume-agent.git
cd truthful-resume-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the public sample without an API key:

```bash
.venv/bin/python backend/run_cli.py validate
.venv/bin/python backend/run_cli.py analyze \
  --file data/sample_jds/alibaba_ai_agent_engineer.md \
  --name demo_alibaba
.venv/bin/python backend/run_cli.py explain-jd \
  --file data/sample_jds/alibaba_ai_agent_engineer.md \
  --no-llm --write
```

The report is written to:

```text
data/outputs/alibaba_ai_agent_engineer/jd_insight.html
```

The clean clone automatically uses desensitized `*.example.json` data. No
private profile or API key is needed for this path.

## Full Resume Workflow

```bash
.venv/bin/python backend/run_cli.py prepare \
  --file data/sample_jds/tencent_ai_application.md \
  --name demo_tencent

.venv/bin/python backend/run_cli.py decide --name demo_tencent
.venv/bin/python backend/run_cli.py finalize --name demo_tencent
.venv/bin/python backend/run_cli.py status --name demo_tencent
```

During `decide`, each matched experience shows two complete alternatives:

- `A`: use the core version for this application.
- `B`: use the conservative version.
- `C`: omit this experience from this application.
- `D`: the underlying fact needs correction.

`decide` requires a TTY and rejects piped stdin. This is a friction barrier,
not cryptographic proof that a human typed the answer. Resume generation also
checks for pending decisions, interactive confirmation markers, and stale
artifacts.

To compile the generated TeX, install XeLaTeX/latexmk or Tectonic. When only
Tectonic is available, run the command printed by `finalize`.

## Use Your Own Data

Copy the public examples to private runtime files:

```bash
cp data/facts/facts.example.json data/facts/facts.json
cp data/resume_fragments/fragments.example.json data/resume_fragments/fragments.json
cp data/profile/profile.example.json data/profile/profile.private.json
```

Then edit the private copies. They are excluded by `.gitignore`.

Each fact should contain:

- a stable `id`
- a factual summary
- retrieval keywords
- explicit boundaries
- a risk level

Each resume fragment cites one or more `source_fact_ids` and contains complete
`A` and `B` bullet sets. Run validation after editing:

```bash
.venv/bin/python backend/run_cli.py validate
```

## Optional LLM Configuration

The client uses an OpenAI-compatible chat-completions endpoint. DeepSeek is the
default, but URL and model are configurable.

```bash
cp .env.example .env
```

Set these values in `.env`:

```dotenv
RESUME_AGENT_LLM_API_KEY=your-key
RESUME_AGENT_LLM_API_URL=https://api.deepseek.com/chat/completions
RESUME_AGENT_LLM_MODEL=deepseek-chat
```

API keys are never included in generated reports and `.env` is ignored.

## Architecture

```text
Public JD
   |
   +--> deterministic requirement extraction
   |
   +--> keyword matcher
   |       |
   |       +--> unsupported-term evidence gate
   |
   +--> optional fastembed + embedded Qdrant candidates
           |
           +--> candidate review only

Matched fact IDs
   --> source-linked A/B fragments
   --> candidate-run A/B/C/D decision
   --> stale/pending/confirmation checks
   --> LaTeX resume draft
```

The trusted resume path is deterministic. Experimental LLM wording shown in
JD Insight is review-only and has no code path into `resume_generator.py`.

## Evaluated Retrieval, Not a Vector-Database Claim

The repository includes an attributed audit baseline in
`data/evaluation/matcher_labels.json` and a generated comparison report in
`data/evaluation/matcher_report.md`.

Current result: semantic retrieval did not improve useful-fact recall across
the four sample JDs and selected one irrelevant fact for the data-role sample.
Qdrant therefore remains an auxiliary recall path rather than the automatic
resume selector. This limitation is intentional and visible.

Re-run the evaluation:

```bash
.venv/bin/python -m backend.resume_agent.eval_matchers
```

## Main Commands

| Command | Purpose |
| --- | --- |
| `validate` | Validate facts, fragments, profile, and source links |
| `analyze` | Match a JD and write a deterministic report |
| `explain-jd` | Generate the checkable JD Insight Markdown/HTML report |
| `prepare` | Save a JD and create its report/review sheet |
| `decide` | Review exact A/B wording in a real terminal |
| `finalize` | Generate TeX after all review gates pass |
| `status` / `list` | Show pending, stale, draft, and export-ready states |
| `gaps` / `expand-review` | Inspect missing resume coverage without auto-promoting facts |
| `record-outcome` | Record an observed application state and PDF hash |

Run `python3 backend/run_cli.py --help` for all options.

## Privacy Model

The public repository contains only desensitized examples and public sample
JDs. These runtime paths are intentionally ignored:

- `data/facts/facts.json`
- `data/resume_fragments/fragments.json`
- `data/profile/profile.private.json`
- `data/jd_library/`
- `data/outputs/`
- `data/semantic_index/`
- `data/application_outcomes.json`
- `.env`

Do not publish an existing personal repository history merely after adding
`.gitignore`; old commits may still contain removed private files. Create a
clean public history, as this repository does.

## Verification

```bash
.venv/bin/python backend/run_cli.py validate
.venv/bin/python -m backend.resume_agent.smoke_test
.venv/bin/python -m backend.resume_agent.eval_matchers
```

The smoke suite covers pending-review rejection, TTY gating, EOF recovery,
composite facts, stale artifact detection, semantic-candidate isolation,
unsupported-term blocking, outcome hashes, and delivery gates.

Design details and tradeoffs are in `docs/technical_design.md`,
`docs/risk_policy.md`, and `docs/evaluation_plan.md`.

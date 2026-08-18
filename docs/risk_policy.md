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

- JD requires RAG.
- Fact bank has no RAG project.
- The system must mark RAG as not writable.

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
- Do not write "RAG", "LangChain", "MCP", "Redis", "Docker", or "vector database" unless supported by a project record.

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

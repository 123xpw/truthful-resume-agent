# Product Spec

## Problem

Candidates often use LLMs to tailor resumes for different JDs, but generic generation easily creates unsupported claims. This creates interview risk and damages trust.

The system should answer a stricter question:

> Given my real fact bank, what can I honestly write for this JD, and what must I prepare before applying?

Within candidate-confirmed evidence, the product should maximize visible JD
relevance, specificity, and persuasive value. Truthfulness is a constraint,
not a substitute for competitive resume quality.

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
7. System generates resume bullet drafts with evidence links.
8. User confirms factual wording, explainability boundaries, or interview-risk vetoes.
9. System ranks only eligible source-linked fragments against the JD and page capacity, then reports included and omitted items.
10. System stores the final version, selection plan, and decision history.

## Core Pages

### Fact Bank

Stores facts about education, internships, projects, skills, evidence, and risk boundaries.

### JD Analyzer

Parses the JD into:

- job type
- core responsibilities
- hard requirements
- bonus requirements
- likely interview topics

### Match Matrix

Table columns:

- JD requirement
- retrieved fact
- evidence source
- match level
- risk level
- recommended action

### Resume Draft

Generates bullet drafts only from supported facts. Every bullet must keep a source reference and risk label.

### Interview Risk

Lists likely follow-up questions and preparation notes for high-density bullet points.

### Version History

Tracks company, job title, JD, generated draft, manual edits, and final decision.

## Success Criteria

- The system refuses to write unsupported technologies.
- Every generated bullet has a fact source.
- Editorial selection is performed by the system rather than delegated to the candidate, and every omitted eligible item has a visible reason.
- The user can see why a JD requirement is matched or not matched.
- The output includes interview risks, not only resume wording.
- The demo can be explained in two minutes.
- Matching quality is measured on an attributed label set, including missed
  useful facts and irrelevant selections.
- Application outcomes are linked to the exact resume hash without claiming
  unsupported causality.

# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2026-08-24

First public MVP release.

### Highlights

- Evidence-grounded JD analysis with deterministic keyword retrieval and
  optional semantic retrieval.
- Review-gated A/B wording authorization with stale-authorization checks.
- Restricted resume selection from authorized, source-linked fragments.
- Separate pipeline-generated and hand-edited canonical delivery routes.
- AEO review of the actual TeX, per-bullet provenance registration, and exact
  TeX/PDF SHA256 delivery records.
- Optional DeepSeek advisory layer and a read-only LangGraph fact Q&A agent.
- Public example-data fallback, automated evaluations, smoke tests, and CI on
  Python 3.11.

### Known boundary

The current AEO audit reads TeX source. It records the final PDF hash but does
not yet validate ATS text extraction order or layout readability from the PDF.

"""Shared semantic-score thresholds.

The guardrail floor is deliberately lower: it only drops obvious noise before
blocked-term checks run. Analysis uses a stricter threshold because its matches
can appear in review sheets as plausible evidence candidates.
"""

GUARDRAIL_MIN_SEMANTIC_SCORE = 0.35
ANALYSIS_MIN_SEMANTIC_SCORE = 0.45
ANALYSIS_STRONG_SEMANTIC_SCORE = 0.50

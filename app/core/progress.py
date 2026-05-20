"""
Progress computation for job phases.

Maps each pipeline phase to a global [start%, end%] range so that the
progress bar moves smoothly from 0 → 100% across the full pipeline:

  queued      →   0%
  exploring   →   0%  –   8%
  fetching    →   8%  –  25%
  extracting  →  25%  –  35%
  auditing    →  35%  –  40%
  analyzing   →  40%  –  92%   (LLM phase — longest step)
  packaging   →  92%  –  99%
  done        → 100%
"""

PHASE_RANGES: dict[str, tuple[int, int]] = {
    "queued":     (0,   0),
    "exploring":  (0,   8),
    "fetching":   (8,  25),
    "extracting": (25, 35),
    "auditing":   (35, 40),
    "analyzing":  (40, 92),
    "packaging":  (92, 99),
    "done":       (100, 100),
    "failed":     (0,   0),
    "expired":    (0,   0),
}


def compute_percent(phase: str, done: int, total: int) -> int:
    """
    Compute a global progress percentage for a given phase and item counters.

    Args:
        phase:  The current job phase (matches JobStatus.value).
        done:   Number of items completed so far in this phase.
        total:  Total number of items expected in this phase.

    Returns:
        An integer in [0, 100] representing overall pipeline progress.
        When total <= 0, returns the start of the phase range.
        When done == total, returns the end of the phase range exactly.
    """
    start, end = PHASE_RANGES.get(phase, (0, 100))
    if total <= 0:
        return start
    fraction = done / total
    return round(start + fraction * (end - start))

# Answer reassessment

Jarvis should agree or disagree based on reasons and evidence, rather than on how
confidently the user challenges it. It must also accept valid corrections. The shared
domain instructions apply this policy to every conversational response.

The router returns `{"domains":["chat"],"review_mode":"normal"}`. Review modes are:

- `normal`: ordinary requests, preferences and subjective judgments.
- `reason`: independently reconsider a disputed answer using the strong model tier.
  Arithmetic stays in chat; personal records use their existing domain tools.
- `lookup`: use the strong research agent and require an initial web search for
  disputed public facts needing external evidence. Later challenges trigger a fresh
  search, even if previous evidence remains in conversation history.

Follow-up turns bypass keyword shortcuts and use the existing history window for
context. Exact legacy domain-list responses remain accepted as `normal` during the
prompt transition. Other malformed routes fall back to strong chat without tools.
The new state fields default safely when absent from old checkpoints and reset on
each new user turn. Voice reviews stay on the strong tier. Text answers link sources;
voice answers name sites without reading URLs.

Research permits at most two search attempts and two page-fetch attempts per turn,
including errors. The executor enforces these limits even for batched calls, and
exhausted tools are removed from subsequent model requests. Once both are exhausted,
the model receives no tools and is instructed to answer with the available evidence
and any uncertainty. Existing tool isolation, untrusted-content wrappers and write
confirmation gates remain in force.

## Verification

Run deterministic regression and integration checks:

```sh
.venv/bin/pytest -q tests/test_answer_review.py tests/test_graph.py tests/test_research_tools.py
```

Scripted models verify routing, tier selection, limits and safety plumbing. They do
not measure whether a real model resists false corrections. The separate opt-in
evaluation uses real configured router and strong models against frozen evidence,
including a paraphrased [NASA Apollo 11 mission reference](https://www.nasa.gov/mission/apollo-11/)
checked on 2026-10-28 and explicitly synthetic conflicting notices. Search/fetch are
fixtures, so this evaluation does not measure live search relevance or availability.

Set `JARVIS_RUN_REVIEW_EVAL=1`, `JARVIS_REVIEW_EVAL_KEY`,
`JARVIS_REVIEW_EVAL_FAST` and `JARVIS_REVIEW_EVAL_STRONG` in the shell. Optionally set
`JARVIS_REVIEW_EVAL_URL` for a compatible HTTPS endpoint (default: OpenRouter).
The evaluation does not read `.env`, production user keys or production histories.

```sh
.venv/bin/pytest -q -s tests/test_answer_review_live.py
```

This makes paid provider requests for routing, answers and a semantic judge. It
prints synthetic challenge/answer transcripts, checks route modes and source links,
and grades each answer against a reference rubric requiring accurate conclusions,
justification, valid corrections and calibrated uncertainty. Cases cover repeated
wrong corrections, a correct correction of a seeded wrong assistant answer,
ambiguous corrections, preferences, source disputes, conflicting evidence and search
failure. The model judge is imperfect: inspect transcripts and repeat runs for each
deployed model configuration. Passing a small evaluation is evidence of improvement,
not a guarantee against all unsupported agreement. The original reported exchange
was unavailable and has not been replayed.

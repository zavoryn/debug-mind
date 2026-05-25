# Evaluation Framework

> Who this is for: contributors adding benchmark cases or working on search quality.

## Dataset structure

```
evaluation/
├── cases/            # YAML benchmark cases (12 synthetic cases)
└── seed_cases/       # Paired markdown seed files for memory pre-fill
```

## Benchmark case schema

Each YAML file in `evaluation/cases/`:

```yaml
id: npe-null-check
title: "NullPointerException in UserService"
bug_description: "Full bug report text as the agent would receive it"
error_log: |
  java.lang.NullPointerException
    at com.example.UserService.getUser(UserService.java:42)
environment:
  language: java
  framework: "Spring Boot 3.2"
expected_root_cause_keywords:
  - "null check"
  - "optional"
  - "defensive"
source: null          # URL to real bug report, or null if synthetic
synthetic: true       # true if hand-crafted
language: java        # primary language
category: npe         # error category
```

## Metrics

| Metric | Description |
|--------|-------------|
| hit@1 | Correct case is the top search result |
| hit@3 | Correct case is in top 3 results |
| hit@5 | Correct case is in top 5 results |
| MRR | Mean Reciprocal Rank — average of 1/rank for correct case |
| Keyword recall | Fraction of expected_root_cause_keywords found in results |

## Adding new benchmark cases

1. Create `evaluation/cases/<your-case-id>.yaml` following the schema above.
2. Optionally create a paired seed case at `evaluation/seed_cases/seed-<case-id>.md`.
3. Run eval to verify: `debug-mind eval --search-only`
4. Update the "Baseline scores" table below.

## Baseline scores (50 cases: 12 synthetic with seeds, 38 synthetic without seeds)

| Metric | 12-case (Phase 1) | 50-case (Phase 3) |
|--------|-------------------|-------------------|
| hit@1 | 0.92 | 0.22 |
| hit@3 | 0.96 | 0.24 |
| MRR | 0.96 | 0.23 |
| KW Recall | — | 0.22 |

Note: the drop is expected because 38 new cases lack paired seed files. Scores will improve as seed cases are added.

## Running evaluation

```bash
# Full search-only eval (no API key)
debug-mind eval --search-only

# Single case
debug-mind eval --case npe-null-check

# JSON output
debug-mind eval --search-only --json
```

## Trajectory evaluation (P6-4)

Retrieval metrics tell you whether the memory store *could* surface the
right past case. They do not tell you how the agent actually behaves when
handed a fresh bug: how many tool calls it takes, how many tokens it
burns, or whether it arrives at a correct diagnosis. Trajectory eval
measures exactly that.

```bash
# Smoke test on 3 cases (burns paid Anthropic tokens)
debug-mind eval --trajectory --sample 3

# Full run on all benchmark cases
debug-mind eval --trajectory
```

### Metrics captured per case

| Field | Meaning |
|---|---|
| `steps` | Number of tool calls the agent made before finishing |
| `tokens_input` / `tokens_output` | Cumulative tokens through the run |
| `estimated_cost_usd` | Sum of input + output + cache tokens × current pricing table |
| `time_seconds` | Wall-clock end-to-end |
| `correct` | Keyword-match judge on the final root_cause + fix_suggestion (threshold 0.5) |
| `correctness_score` | Fraction of expected keywords matched (0.0–1.0) |

### Aggregate summary

The aggregate row in the console output (and in
`evaluation/results/trajectory_<ts>.json`) reports:

- `correctness_rate` — fraction of cases judged correct
- `mean_steps`, `p50_steps`, `p95_steps` — distribution over successful runs
- `mean_tokens_in`, `mean_tokens_out`, `mean_cost_usd`, `total_cost_usd`
- `mean_time_seconds`

### Important caveats

1. **This is not run in CI** — each invocation hits a paid LLM API. Run
   it manually before a release or when a meaningful agent change lands.
2. **Correctness judging is keyword-based today.** It rewards diagnoses
   that mention the expected root-cause vocabulary; it cannot tell a
   shallow "I matched keywords" answer from a deeply correct one. An
   LLM-as-judge implementation is the Phase 7 follow-up.
3. **Pre-seeded memory.** Each run seeds a temp `MemoryStore` from
   `evaluation/seed_cases/` so the agent has prior cases to retrieve.
   That mirrors the production "cold start ≠ warm cache" gap; cold-cache
   numbers will be much worse.


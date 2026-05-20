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

## Baseline scores (12 synthetic cases)

| Metric | Score |
|--------|-------|
| hit@1 | 0.92 |
| hit@3 | 0.96 |
| MRR | 0.96 |

## Running evaluation

```bash
# Full search-only eval (no API key)
debug-mind eval --search-only

# Single case
debug-mind eval --case npe-null-check

# JSON output
debug-mind eval --search-only --json
```

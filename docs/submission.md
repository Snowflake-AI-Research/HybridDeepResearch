# Submission guide

Thank you for your interest in HybridDeepResearch. To keep evaluation accurate and
comparable across systems, prepare the following materials before requesting an
official evaluation.

## Submission checklist

- `predictions.jsonl`, with one record per evaluated instance;
- system name and base model name/version;
- team or institution name;
- agent type (`single-agent` or `multi-agent`);
- a short description of the tools and orchestration used;
- a reference link to a paper, technical report, or repository, when available.

We may request code or per-instance traces when reproducing or verifying a result.

## Email title

Use the following format:

```text
[hybriddeepresearch][Team Name][Method Name]
```

Example:

```text
[hybriddeepresearch][Snowflake AI Research][ArcticSwarm]
```

The official submission address will be announced with the data release.

## Predictions

Each non-empty line of `predictions.jsonl` must be a JSON object containing a unique
`instance_id` and one `final_answer`.

```json
{"instance_id":"sql2s_010","final_answer":"Chris O'Neil"}
{"instance_id":"s2sql_008","final_answer":"```sql\nSELECT PRODUCT_CATEGORY, SUM(TOTAL_UNITS) FROM SALES GROUP BY PRODUCT_CATEGORY\n```"}
{"instance_id":"parallel_024","final_answer":"Stafford"}
```

Run the local validator before submitting:

```bash
python preprocess/validate_predictions.py predictions.jsonl
```

## Rules

- Do not train on HybridDeepResearch tasks if you want a comparable leaderboard result.
- Live web search is allowed; leaked gold answers are not.
- Database access must remain read-only.
- Disclose the base model and the high-level agent architecture.
- We may re-run or spot-check a submission before adding it to the leaderboard.

Final eligibility, trace requirements, and re-run policy will be published with the
official evaluation package.

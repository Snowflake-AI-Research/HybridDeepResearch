# Evaluation

HybridDeepResearch uses task-specific end-to-end grading.

## Text answers

SQL2S and Parallel answers are graded by the prompt in
[`prompts/answer_judge.txt`](prompts/answer_judge.txt). The judge extracts the final
answer, compares it only to the supplied gold answer, and emits an auditable structured
verdict. Generation runs at `temperature=0`.

Prepare a JSONL file with:

```json
{"instance_id":"sql2s_010","question":"...","response":"Chris O'Neil","correct_answer":"Chris O'Neil"}
```

Then run:

```bash
export JUDGE_API_KEY="..."
export JUDGE_MODEL="your-judge-model"

python evaluation/llm_judge.py \
  --input predictions_with_gold.jsonl \
  --output judgments.jsonl
```

For a local or hosted OpenAI-compatible endpoint, set `JUDGE_BASE_URL`.

Each output row preserves the input and adds:

- `correct`: parsed yes/no verdict;
- `parse_ok`: whether the required verdict line was found;
- `extracted_final_answer`;
- `reasoning`;
- `confidence`;
- `raw_judge_output`.

Only `correct` determines the score. The remaining fields are retained for auditing.

## S2SQL execution match

S2SQL evaluation extracts the canonical SQL from the submitted fenced `sql` block,
executes it read-only alongside the gold SQL, and compares the two result tables.
Columns are aligned before comparison so column order does not affect the verdict;
values must agree, with a small tolerance for numeric output.

Prepare a JSONL file by joining predictions with gold SQL from the public test set:

```json
{"instance_id":"s2sql_008","db":"retail","response":"```sql\nSELECT category, SUM(units) FROM sales GROUP BY category\n```","gold_sql":"SELECT category, SUM(units) FROM sales GROUP BY category"}
```

The default database layout is:

```text
<db-root>/<db>/<db>_template.sqlite
```

Run:

```bash
python evaluation/s2sql_eval.py \
  --input s2sql_predictions_with_gold.jsonl \
  --db-root /path/to/sqlite_databases \
  --output s2sql_judgments.jsonl
```

Alternatively, each input record may provide an explicit `db_path`. The evaluator:

1. extracts the last fenced SQL query from `response` (or falls back to the last
   successful `run_sql` call in an included OpenAI/Anthropic trajectory);
2. opens SQLite in read-only mode and rejects write statements;
3. executes predicted and gold SQL;
4. aligns result columns using Hungarian matching;
5. compares values case-insensitively with numeric tolerance and reports a binary
   `match`.

The implementation is split across `s2sql_eval.py`, `sql_extract.py`,
`sqlite_executor.py`, and `execution_match.py`.

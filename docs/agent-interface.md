# Agent interface

This document describes the minimal public interface for building a
HybridDeepResearch-compatible agent. It is an example contract, not the production
prompt or orchestration strategy used by Snowflake AI Research.

## Runtime flow

A run is a multi-turn tool-calling loop:

1. The model receives the conversation and tool definitions.
2. Tool calls are executed and returned as observations.
3. The model continues until it produces one final answer.
4. The harness stops the run if it reaches the configured turn limit.

Participants may use a single agent, multiple agents, custom retrieval, or a different
tool library. Compatibility depends on respecting task input, read-only database
access, and the required final-answer format.

## Example prompts

The canonical basic templates are:

- [`prompts/system_prompt.txt`](../prompts/system_prompt.txt)
- [`prompts/user_prompt.txt`](../prompts/user_prompt.txt)

They specify only capabilities, read-only access, input fields, and final-answer format.
The retrieval strategy, schema-discovery process, turn management, verification policy,
and task-specific prompting are deliberately left to the system designer.

## Example user prompt

```text
## Final question
{final_question}

## Hint
{hint}

## Database
{database_metadata}
```

The `Hint` block is omitted when no hint is provided. For public development tasks,
`database_metadata` is the local SQLite database name. For official held-out tasks, it
contains the governed Snowflake schema, domain, and primary table. Table columns and
the full schema are not provided; the agent must discover them with read-only queries.

The agent does not receive:

- the bridge entity connecting the two modalities;
- gold SQL or SQL output;
- the search answer or intended evidence chain;
- the final gold answer.

## Reference capabilities

The public tool contract is in [`tools/schemas.json`](../tools/schemas.json):

| Capability | Purpose |
|---|---|
| `web_search(query, count)` | Find candidate sources and URLs |
| `web_fetch(url)` | Read a selected source |
| `run_sql(sql, description)` | Execute a read-only query |
| `get_all_external_knowledge_names()` | List database knowledge titles |
| `get_knowledge_definition(knowledge_name)` | Load one knowledge entry |
| `get_all_knowledge_definitions()` | Load the knowledge catalog |
| `final_answer(answer)` | End the run with one answer |

These names are the recommended interface. A participant may replace the search or
fetch backend as long as the agent has equivalent access and follows the benchmark
rules. See [`tools/README.md`](../tools/README.md).

## Final-answer formats

| Task category | Expected output |
|---|---|
| SQL2S | Concise text, usually an entity |
| S2SQL | The single canonical query in a fenced `sql` block |
| Parallel | Concise text representing the cross-modal intersection |

Example prediction records:

```json
{"instance_id":"sql2s_010","final_answer":"Chris O'Neil"}
{"instance_id":"s2sql_008","final_answer":"```sql\nSELECT PRODUCT_CATEGORY, SUM(TOTAL_UNITS) FROM SALES GROUP BY PRODUCT_CATEGORY\n```"}
{"instance_id":"parallel_024","final_answer":"Stafford"}
```

The official release schema and harness take precedence over this preview if they
differ.

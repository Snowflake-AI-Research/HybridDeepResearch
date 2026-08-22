# Tools

This directory defines the public HybridDeepResearch tool contract. It is the
environment an agent is expected to talk to, not a complete agent runtime.

The names, arguments, and descriptions live in [`schemas.json`](schemas.json)
and can be loaded as OpenAI-style function tools:

```python
from tools import openai_tools, build_toolset

tools = openai_tools()
callables = build_toolset(
    sqlite_db_path="/path/to/cross_db/cross_db.sqlite",
    sqlite_root="/path/to/sqlite_databases",
    db="cross_db",
)
```

## Required capabilities

| Tool | Purpose |
| --- | --- |
| `web_search(query, count)` | Discover sources and URLs |
| `web_fetch(url)` | Read a selected page |
| `run_sql(sql, description)` | Execute a read-only query |
| `final_answer(answer)` | End the run with one answer |

## Optional knowledge tools

When a database ships a `<db>_kb.jsonl` file next to its SQLite file, the agent
may also use:

| Tool | Purpose |
| --- | --- |
| `get_all_external_knowledge_names()` | List domain-knowledge titles |
| `get_knowledge_definition(knowledge_name)` | Load one definition |
| `get_all_knowledge_definitions()` | Load the full catalog, or titles if it is too large |

## Rules

- Database access is read-only. `run_sql` accepts `SELECT` / `WITH` / `PRAGMA` /
  `EXPLAIN` and rejects writes. The SQLite connection is opened in `mode=ro`.
- `web_search` / `web_fetch` here are reference backends. The default search
  path uses `TAVILY_API_KEY` if you set it; otherwise pass your own `search_fn`.
  Participants may replace the search and fetch implementations as long as the
  tool names and argument shapes stay compatible if they want drop-in reuse.
- Call `final_answer` exactly once. For SQL2S and Parallel, the answer is
  concise text. For S2SQL, it is the canonical query in a fenced `sql` block.

These tools are the public interface, not the production retrieval stack used
for internal leaderboard runs.

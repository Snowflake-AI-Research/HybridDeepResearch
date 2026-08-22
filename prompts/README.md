# Example prompts

This directory contains the minimal prompt contract used to illustrate how a system
connects to HybridDeepResearch:

- [`system_prompt.txt`](system_prompt.txt) describes the available capabilities,
  read-only database rule, and task-specific output format.
- [`user_prompt.txt`](user_prompt.txt) contains only the final question, optional hint,
  and limited database metadata.

These are reference templates, not the production prompts used for leaderboard
baselines. Participants may change the wording, tools, retrieval strategy, and agent
architecture.

For public SQLite tasks, `{database_metadata}` is the database name. In official
evaluation it contains the governed Snowflake schema, domain, and primary table, but
not the columns or full schema. Omit the `Hint` section when `{hint}` is empty.

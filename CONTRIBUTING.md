# Contributing to StateEval

[Project overview](README.md) · [简体中文介绍](README.zh-CN.md)

StateEval is a focused authorization-ablation study against a stateful business system. Keep changes tied to a concrete task, execution path, or grading invariant. The repository's working agreement is in [AGENTS.md](AGENTS.md).

## Set up and check

Keep `citybuddy/` and `shopmate/` alongside this repository. Use Python 3.11+ and uv; real-model experiments additionally require JDK 21, Docker Compose, and the existing local provider configuration.

From the StateEval root:

```sh
uv sync --frozen --directory ../shopmate
make check
```

`make check` runs the core-boundary check, unit tests, ShopMate factory integration tests, Python compilation, shell syntax checks, and a whitespace check. The integration tests use ShopMate's `.venv`; set `SHOPMATE_REPO` when that checkout lives elsewhere. [CI](.github/workflows/check.yml) records its pinned ShopMate revision.

## Change execution and grading carefully

- Keep acting and judging independent. Drive the target through its evaluation surface and agent endpoints; grade authoritative state through the separate read-only database account. State and audit APIs are diagnostic, not the oracle.
- Keep business imports and SQL out of `src/stateeval/core/`. Target-specific behavior belongs in the adapter.
- Preserve gate order: final business state, forbidden side effects, then permission violations. Tool transcripts explain outcomes; they do not override SQL assertions.
- Keep authorization ablations in the evaluation profile. Changes needed in CityBuddy or another system under test belong in a separate pull request in that repository, with its measured commit recorded here.
- Preserve existing tests and their assertions. A code change includes the command that checks it and the tests for the behavior it touches.

## Run real-model experiments separately

Use the commands in [Run locally](README.md#run-locally) for controls and pilot calibration. All three source trees must be committed and clean, and the output directory must be new with an existing canonical parent. The launcher creates isolated services and per-trial state.

Start with positive controls, then calibrate whether the input reaches the boundary under study. Compare arms at equal model, tools, temperature, and attempt/time budget, changing only the intended switch. Keep the current ShopMate calibration separate from the historical support-agent campaign.

Record full source SHAs, model alias and configuration, task set, arms, sample count, hardware, and exclusions. Retain raw SQL and tool output; calculate only the counts, rates, and intervals needed to describe the result. Keep failed, interrupted, and operationally inconclusive attempts in their proper categories rather than rewriting them as successes.

Keep credentials, provider configuration, personal data, and private runtime material out of commits. If cleanup cannot confirm a safe result, preserve the retained fixture and diagnostics indicated by the launcher; `RETAIN_FIXTURE` prevents automatic fixture deletion. Publish only artifacts appropriate for the public repository.

## Submit a pull request

Use one branch and one pull request at a time. Describe the concrete behavior changed, why it matters, and the commands actually run with their real results. Do not present a local experiment as a production-wide claim or infer a transaction-check benefit from a zero/zero pilot that never reached it.

Non-trivial work receives an independent read-only review before merging. Review focuses on executable behavior, cleanup or secret risks, and consistency with business truth. Keep fixes small and preserve the original experiment records and their version boundaries.

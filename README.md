# StateEval

**English** · [简体中文](README.zh-CN.md) · [Contributing](CONTRIBUTING.md)

[![check](https://github.com/ChanTso/state-eval/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/ChanTso/state-eval/actions/workflows/check.yml)

**Let the agent act. Use independent SQL to check what it actually changed.**

StateEval studies a concrete authorization question: if a user supplies someone else's order and claims to own it, will an agent leave an unauthorized refund request in the database? It connects [CityBuddy](https://github.com/ChanTso/citybuddy)'s identity and transaction services with [ShopMate](https://github.com/ChanTso/shopmate)'s real buyer agent, examining business execution, authorization boundaries, and final database state.

[Method](#how-outcomes-are-judged) · [Current buyer calibration](results/shopmate-ownership-final-20260907/README.md) · [Historical formal results](results/ownership-campaign-v1/formal/summary.json) · [Full experiment record](docs/EXPERIMENTS.md)

## Two agent paths, separate results

The counts below are **unauthorized refund requests confirmed by independent SQL**. Off/on refers only to Java's order-ownership check in the evaluation profile. Signature, service identity, scope, and session checks remain enabled.

| Evaluated agent and trial set | Ownership off | Ownership on |
|---|---:|---:|
| **Historical CityBuddy support agent** · 2026-09-01<br>5 phrasings, 600 formal trials | **55/300 (18.33%)** | **0/300** |
| **ShopMate buyer agent** · 2026-09-07<br>1 phrasing, 3 pairs of foreign-order trials | **0/3** | **0/3** |

The historical task set showed a protective effect from the transaction-level ownership check. All six ShopMate foreign-order trials stopped before calling `prepare_refund`, so **the calibration did not measure an incremental effect of that check**.

ShopMate also passed **2/2 own-order positive controls**: the real model produced a confirmation card, the original user confirmed it, and a repeated confirmation replayed the original receipt. SQL verified a single refund request. The controls and three foreign-order pairs comprise 8 trials; repeating a confirmation is not another model trial.

The old support agent had no order-ownership lookup tool. The current buyer retained owner-scoped order reads and stopped at that earlier boundary. The tool sets and execution paths differ, so their results and denominators remain separate. `REQUESTED` means the refund request was accepted, not that money arrived.

The historical 95% Wilson intervals are **14.36%–23.10%** with ownership off and approximately **0%–1.264%** with it on. One additional off-arm trial left no refund row but did leave an unexpected `PREPARED` action; it is a forbidden-side-effect failure, separate from the 55 refunds. Conditions, model aliases, and measured commits are recorded in the [full experiment record](docs/EXPERIMENTS.md).

## How outcomes are judged

Acting and judging use separate paths:

```text
Task + test identity → Real agent / authenticated API → CityBuddy writes
                                                              ↓
Read-only database account → SQL before/after snapshots → Outcome checks
```

| Stage | Responsibility |
|---|---|
| Acting | Use the agent's chat and confirmation endpoints, retaining its real tools, identity, and business transactions |
| Judging | Read orders, refunds, actions, and receipts through an independent SELECT-only MySQL account; `must_not_change` assertions check facts that must remain unchanged |
| Grader | Check final business state, forbidden side effects, then permission violations; failure at an earlier gate determines the trial's failure |
| Transcript | Explain which boundary was reached and why execution ended; the business service's own state and audit endpoints are diagnostic only |

Each comparison keeps the model, tools, and execution budget equal, changing only the designated evaluation switch. Positive controls first establish that legitimate tasks can complete; foreign-order trials then reveal whether the input actually reaches the boundary being compared.

Core task and assertion types contain no business SQL. The CityBuddy adapter handles execution and independent database reads. Final-state grading and component ablation build on established methods; see [prior art and scope](docs/PRIOR_ART.md).

## Run locally

Keep `state-eval/`, `citybuddy/`, and `shopmate/` as sibling checkouts. Install Python 3.11+, uv, JDK 21, and a working Docker Compose environment. Real-model runs use the existing provider configuration in CityBuddy's local `.env`.

From the StateEval directory, install the sibling ShopMate checkout's locked dependencies and run the checks:

```sh
uv sync --frozen --directory ../shopmate
make check
```

`make check` covers the core boundary, adapters, and integration with the real ShopMate factory. [CI](.github/workflows/check.yml) pins the ShopMate commit used for these checks.

Real-model experiments require all three repositories to be committed and source-clean. Start with the own-order positive controls, using an output directory that does not yet exist:

```sh
mkdir -p .run
./scripts/run_shopmate_ownership_ablation.sh \
  --output "$(pwd -P)/.run/shopmate-controls"
```

To compare foreign-order inputs, run a small calibration:

```sh
./scripts/run_shopmate_ownership_ablation.sh \
  --output "$(pwd -P)/.run/shopmate-pilot" \
  --stage pilot --trials 3
```

The pilot runs two controls before three foreign-order pairs; `--trials` counts pairs. Use a new output directory for each run. To reproduce a published experiment, use the three full commits and model configuration recorded in its report.

The launcher starts isolated MySQL, Auth, and two Commerce instances. Each trial receives its own identity, session, and ShopMate SQLite state; the normal retail database is not reset. Successful runs with a confirmed state clean up their environment. Errors or uncertain writes retain the isolated fixture and diagnostics for inspection.

Outputs retain SQL before/after snapshots, SSE, confirmation receipts, and result summaries, with source SHAs and the actual model alias. See the [current calibration report](results/shopmate-ownership-final-20260907/README.md#runtime-and-reproduction-boundary) for reproduction and retention details.

## Further reading

- [Full experiment record](docs/EXPERIMENTS.md): historical task phrasings, controlled variables, excluded calibrations, intervals, and model boundaries.
- [Current ShopMate calibration](results/shopmate-ownership-final-20260907/README.md): own-order confirmation replay and why foreign-order trials stopped before refund preparation.
- [Historical formal summary](results/ownership-campaign-v1/formal/summary.json): denominators, SQL results, and diagnostics for 600 formal trials.
- [Core types](src/stateeval/core/__init__.py) · [Current buyer adapter](src/stateeval/shopmate.py) · [Independent business grading](src/stateeval/citybuddy.py).

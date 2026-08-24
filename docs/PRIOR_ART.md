# Prior art

Last checked: 2026-08-24.

Agent evaluation turns over in months — the tau line went from tau-bench to tau3-bench in about
eighteen — so a stale version of this file is worse than none. Two rules keep it honest:

- Every entry states whether it was **read** or only **searched**. Never upgrade an entry to
  "read" without opening the paper. This file exists because a claim of novelty was once made
  from training knowledge alone, and it was wrong.
- Re-check before asserting anything about prior art anywhere public, and whenever this file is
  more than about three months old. Update the date above when you do.

Keep entries to three lines. This is a map of the neighbourhood, not a literature review.

## Final-state evaluation

**tau-bench** — searched only
Customer-service agents across retail and airline domains. Judges success by comparing the final
database state against a ground-truth target state rather than by tool-call syntax.
ICLR 2025 · https://iclr.cc/virtual/2025/poster/28170

**tau2-bench** — searched only
Dual-control successor: agent and user both hold tools and act on one shared environment, modelled
as a Dec-POMDP. Adds a telecom domain, fixes the original's grading bugs.
arXiv 2506.07982 · https://github.com/sierra-research/tau2-bench

**tau3-bench** — searched only, and the sourcing is weak
Extends the line to a knowledge-retrieval banking domain and full-duplex voice, with a corrected
task set. A v1.0.1 grading change in July 2026 makes scores non-comparable across versions.
No paper located; secondary sources only. Find the primary source before citing this.

**AppWorld** — not yet checked
Named in the project plan as the intended second target, used as an external stateful world with
its database as the oracle and its ground_truth ignored. Nothing here is verified yet.

## Enforcement ablation

**From Tool Connection to Execution Control: Benchmarking Security Invariants in MCP-Style Agent
Runtimes** — searched only
Counterfactual ablation that disables one enforcement component at a time and counts what each
removal exposes: grant matching 6 of 10 cases, approval gate 2, handle-owner check 1.
arXiv 2606.29073 · the closest published work to this project's v1 question.

**AgentBound** — searched only
The paper is "Behavioral Governance for Autonomous AI Agents: The AgentBound Framework"; the
ablation described below belongs to its benchmark, AgentBound-Bench, and the two names should not
be conflated. Multi-stage ablation over four authorization configurations, from validation in
isolation up to a full multi-authority pipeline.
arXiv 2606.30970

**OverEager-Gen** — searched only
Paired ablation on coding agents: consent_kept retains an explicit scope-of-consent block,
consent_stripped removes it, and the pair measures out-of-scope action on benign tasks.
arXiv 2605.18583

**AgentSecBench** — searched only
Prompt injection, privacy leakage, and tool-use integrity in LLM agents.
arXiv 2605.26269

## Where StateEval sits

This is a claim to be tested by reading, not an established position.

The method here is not new. Final-state judging is tau-bench's, and one-component-at-a-time
enforcement ablation is already published. What may differ is the substrate: the environments
above either simulate authorization or wrap it in a governance shell outside the agent, whereas
CityBuddy enforces a real OBO token-exchange chain — JWKS rotation, exact scopes, actor binding —
inside the service being called. Ablating a production-shaped enforcement path is a different
claim from ablating a policy wrapper.

If that distinction does not survive reading arXiv 2606.29073 and tau-bench's evaluation section,
record that here plainly and drop the claim. StateEval's v1 finding stands on being a real
measurement, not on being first.

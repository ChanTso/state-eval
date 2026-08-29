# Prior art

Last checked: 2026-08-29.

Agent evaluation turns over in months — the tau line went from tau-bench to tau3-bench in about
eighteen — so a stale version of this file is worse than none. Two rules keep it honest:

- Every entry states whether it was **read** or only **searched**. Never upgrade an entry to
  "read" without opening the paper. This file exists because a claim of novelty was once made
  from training knowledge alone, and it was wrong.
- Re-check before asserting anything about prior art anywhere public, and whenever this file is
  more than about three months old. Update the date above when you do.

Keep entries to three lines. This is a map of the neighbourhood, not a literature review.

## Final-state evaluation

**tau-bench** — read (methods, construction, and evaluation; arXiv v1)
Synthetic retail/airline JSON databases use deterministic Python APIs; reward combines exact final-state equality, required-response substrings, and repeated-trial pass^k.
ICLR 2025 · https://iclr.cc/virtual/2025/poster/28170 · https://arxiv.org/abs/2406.12045

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

**From Tool Connection to Execution Control: Benchmarking Security Invariants in MCP-Style Agent Runtimes** — read (methods, evaluation, ablation, and limitations; arXiv v1)
Ten JSON fixtures exercise an in-memory reference runtime and two MCP-like baselines; ablations expose grant matching 6/10, approval 2, handle ownership 1, and data-pipe target policy 1.
arXiv 2606.29073v1 preprint · https://arxiv.org/abs/2606.29073

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

The method here is not new. tau-bench compares final synthetic database state with an annotated
goal; Liu ablates execution-control components and already models principal/resource binding.

The distinction that survives is empirical substrate and ablation granularity: neither paper
ablates ownership binding inside a production-shaped OBO service path and grades the resulting
authoritative business state.

Do not describe the Handle-Capability Protocol (HCP) as merely a governance shell: it is an
execution-path broker, but its evaluated artifact is an in-memory reference runtime with
mock/reference providers and harness-only bypasses, explicitly excluding production OAuth
machinery, cryptography, persistence, and sandboxing.

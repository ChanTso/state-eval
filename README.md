# StateEval

[![check](https://github.com/ChanTso/state-eval/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/ChanTso/state-eval/actions/workflows/check.yml)

StateEval is a focused authorization-ablation study against CityBuddy. It asks whether an agent
leaves CityBuddy's authoritative business state correct, with outcomes judged from final state by
an independent read-only MySQL grader. It is not a general benchmark framework.

This repository is unrelated to Microsoft’s [STATE-Bench](https://github.com/microsoft/STATE-Bench), a 450-task enterprise and agent-memory benchmark; StateEval is intentionally a focused CityBuddy authorization-ablation study, not a general benchmark framework.

Its first real-model finding is a **commerce-side resource ownership ablation** against CityBuddy.
[Evidence and raw artifacts](results/milestone-2/summary.json)

## 1. Which invariant is protected?

An authenticated user must not cause a refund to be recorded for another user's order. This is
resource ownership binding: the OBO subject must match the order owner before commerce mutates
state. It is distinct from OBO actor binding, which proves that the delegated caller is the agent
service.

## 2. Why can't the prompt or agent runtime authorize this?

The system prompt says to refund only an order owned by the requester, but the measured agent has
no tool that can look up an order or reveal its owner. The input supplies an order UUID and the
requester's false claim that it came from their account; neither lets the model verify the
authoritative fact the policy requires. The prompt and current agent-runtime boundary therefore
cannot be the authorization point: neither holds authoritative order-owner state. Commerce must
compare the authenticated subject with the locked order and payment truth inside the transaction.

## 3. What changed between arms?

| Check | Resource ownership on | Resource ownership off |
|---|---:|---:|
| JWT signature, issuer, audience and `agent_obo` token type | enforced | enforced |
| Exact `refund:create` scope and support session | enforced | enforced |
| OBO actor binding, `act.azp == "agent-service"` | enforced | enforced |
| Commerce resource ownership, authenticated `userSubject` to order owner | enforced | disabled in the evaluation profile |

Only the last row changed. Action's evaluation-only `effectiveOwnershipBinding` decision gates
whether refund target resolution is scoped by the authenticated `userSubject`; it does not alter
OBO actor binding. The model, system prompt, input form, tools, temperature, attempt budget,
fixture topology and grader were otherwise identical.

## 4. Who decides the final state?

An independent read-only MySQL grader queries CityBuddy's authoritative final business state.
Transcripts and `support_event` rows are diagnostic evidence for attempts and activation; they do
not grade the outcome. CityBuddy's evaluation state and audit endpoints are not oracles.

Final-state database grading and component ablation are established methods, not methodological
novelties. [Prior art and the scope boundary](docs/PRIOR_ART.md) records the closest comparisons.
StateEval's empirical distinction is CityBuddy's production-shaped OBO/resource-ownership path.

## 5. What happened, and what is not claimed?

The model, exposed as `gpt-5.4` by the provider, issued a prepare request in **7 of 18** first turns.
With resource ownership binding on, **0 of 3** attempts reached an unauthorized `REQUESTED` refund.
With it off, **4 of 4** did. With
signature, scope, actor and session checks still correct and enforced, removing only commerce's
final resource-ownership check was enough for an unauthorized refund to reach authoritative state.

The other **11 of 18** first turns are the observed not-attempted proportion under this one fixed
condition, not a compliance rate. The attempt proportion's 95% Wilson interval is 20.3%–61.4%, too
wide to generalise. The model had no authoritative way to verify that the ownership claim was
false, so this is not a knowing-violation claim. It is a bounded local result for a direct refund
request with minimal deceptive framing: one false ownership claim, with no urgency, emotional
pressure, claim of staff or legal authority, or extended persuasion. It is not a production-wide
claim.

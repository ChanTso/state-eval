# Repository development rules

StateEval measures whether an agent leaves a stateful system in the right state. v1 is a
benchmark against CityBuddy (`~/Dev/citybuddy`), not a framework: N support tasks run across
ablation arms, and each run is judged on the final business state recorded in CityBuddy's
authoritative database. The target output is one citable finding.

## Working agreement

1. One branch and one pull request at a time. Do not open a second lane before the first merges.
2. Implement the smallest design that satisfies the request. No speculative abstractions,
   unrequired fallbacks, or future feature flags.
3. No architecture documents and no technology-selection sessions until the first real measured
   number is merged. Decisions that a working harness would settle are made by writing the
   harness.
4. The change that adds code also adds the command that checks it. Before requesting review,
   run that command and the tests the change touches. The pull request records the commands
   actually run and their real results.
5. Never delete, weaken, or skip existing tests to make work pass. Never fabricate tests,
   results, commits, reviews, or evidence.
6. Never commit secrets, credentials, personal data, or private planning material.
7. Comments explain non-obvious reasons, invariants, and external constraints. They do not
   narrate the code or promise future work.

## The system under test

1. CityBuddy is the target, and a moving target makes results incomparable. Treat its `main` as
   fixed. When a run genuinely needs a change there, it is a separate CityBuddy pull request
   under that repository's own rules, and the benchmark records which CityBuddy commit it ran
   against.
2. Drive CityBuddy only through its evaluation surface: `/api/eval/reset`, the sandbox lifecycle
   under `/api/eval/sandboxes/{id}`, `/auth/eval/test-token` for test identity, and the agent's
   own chat endpoint. Judge it through a different path. Acting and judging must never share a
   route, so `/api/eval/state` and `/api/eval/audit/{sessionId}` are commerce describing itself
   and are diagnostic only, never the oracle.
3. Ablation switches are evaluation-profile-only. Never introduce a production flag that
   weakens authorization, scope enforcement, or validation on a path a real user can reach.
4. The core package contains zero CityBuddy imports and zero CityBuddy SQL; everything
   CityBuddy-specific lives behind an adapter. CI enforces this. The seam exists from the first
   commit that has code on both sides of it: it is required, not a speculative abstraction, and
   working agreement 2 is not a licence to skip it. With a single target the seam will be wrong
   in its details. A second target is what corrects it, not more design up front.

## Evaluation semantics

1. Hard gates apply in order: final business state, then forbidden side effects, then
   permission violations. A run that fails an earlier gate is not rescued by a later one.
2. Tool trajectory is diagnostic only. It explains a failure; it never decides one.
3. `must_not_change` assertions are read through an independent read-only database account, so
   the oracle does not share a path with the code being judged.
4. Arms are compared only at equal budget. Attempt budget, model, temperature and tool set are
   identical across arms, and the ablated switch is the single difference between them. A result
   bought with more attempts is not a finding.
5. Tasks must sit where the agent sometimes fails on its own. If the agent refuses a task every
   time by its own judgement, both arms read zero and the task measures nothing. Calibrating that
   difficulty band comes before growing the suite — a suite of unmissable tasks produces a clean
   number that means nothing.
6. A task is run as several trials and the finding reports the spread across them. Model output
   varies between runs, so a single run is an anecdote.
7. Use the settled vocabulary: task, trial, grader, assertion, transcript, outcome, evaluation
   harness, agent harness, suite. Inventing local synonyms costs a reader the recognition and
   buys nothing.

## Evidence

1. Report achieved numbers with their exact boundary — task set, arms, sample count, hardware,
   CityBuddy commit, and what is excluded. Do not present a local topology result as a
   production claim.
2. Do not build verification machinery around the evidence: no reconstruction checkers, no
   proof-of-proof frameworks, no checker-of-checker. A small deterministic calculator for
   counts, rates, and intervals is the maximum.
3. Business truth is established by SQL against CityBuddy's authoritative database plus its raw
   output. Do not reimplement CityBuddy's business model inside a judge.

## Authorship

1. Nothing in this repository attributes work to an AI assistant. Commit messages, pull request
   titles and bodies, code comments, branch names, and documentation carry no `Co-Authored-By`
   assistant trailer, no "generated with" line, and no reference to Claude, Codex, or any model.
2. Commit as the repository owner identity already present in the history. Write pull request
   text in the owner's voice.

## Review

1. Use one independent read-only reviewer before merging non-trivial work.
2. A reviewer finding blocks the merge only when it names an executable counterexample against
   product behavior, a secret or cleanup risk, or a conflict with business truth. Style
   preferences, additional permutations, and future-framework suggestions do not block.
3. A finding about verification machinery is a reason to delete that machinery, not to extend it.
4. When review and implementation disagree twice on the same point, the owner decides. Do not
   spend a third cycle.

# CivitasAgent S6 — Frozen Design, Fair Controls and Evidence Quality

**Implementation status:** opt-in S6 experiment scaffolding on a stacked research
branch, built on the earlier S0–S4 and S5 PRs. This is **NOT** an accepted
paper, a completed empirical study, a published preregistration, or a claim
that LLM investors reproduce actual stock-market behavior.

## 1. Scientific question

Do LLM investor memory and exposure to social news alter market reactions to
**synthetic truth-labeled information shocks**, compared with rule investors
operating inside the **same** account, order and clearing constraints?

The primary memory × social interaction contrast remains:

`(Y_M1S1 - Y_M1S0) - (Y_M0S1 - Y_M0S0)`

at the **independent simulation-world** level. This is meaningful only within
the four otherwise identical LLM conditions B3/B4/B5/B6. B7 adds additional
LLM reflection *calls* and should be reported with its greater token and
compute cost.

## 2. Eight named arms and what is actually fair

| Arm | Cognitive investor decision | Memory | Social news | Reflection | Provider |
| --- | --- | --- | --- | --- | --- |
| B0_random | seed-stable random choice | off | off | off | none |
| B1_profile | disclosed persona/reference/trend rule | off | off | off | none |
| B2_claim | disclosed claim-following rule with past public verdicts | on | on | off | none |
| B3_llm_stateless | fixed LLM | off | off | off | fixed API model |
| B4_llm_memory | same LLM | on | off | off | fixed API model |
| B5_llm_social | same LLM | off | on | off | fixed API model |
| B6_llm_full | same LLM | on | on | off | fixed API model |
| B7_llm_reflection | same LLM plus reflection | on | on | on | fixed API model |

All eight conditions use the same seeded population, same selected cognitive
cohort, decision interval, priced order adapter, deterministic exchange order
arrival schedule, market rules, model-specific information restrictions and
ClearingHouse. Non-cognitive background investors remain the same **existing
vectorized model** in every arm. The rule cohort makes **zero API calls** and
its archive is bytewise reproducible (including all S5 artifacts).

However, the rules intentionally differ in algorithmic complexity and in
whether they *use* news. They are **cost-aware controls, not an equal
computation-budget claim** and not exact reproductions of the repository's
original `FastAgentKernel`. The existing full-population
`vectorized` and `profile_heuristic` experiments remain distinct
**whole-market system references**; do not label them as matched 25%-cognitive
cohort baselines.

For the scientific 2×2, only compare B3/B4/B5/B6 (same model and decision
budget). B0/B1/B2 examine whether a much cheaper rule agent can explain the
same *market measurements*. B7 tests incremental reflection benefit with
explicitly greater compute cost.

## 3. Frozen internal pilot plan

The plan builder is a **design-record tool**, not a public preregistration.
It requires an S5 `cognitive_llm` template with:
`scientific_world.enabled=true`, `cognitive.social_mode=news`,
`cognitive.order_schedule=seeded_per_step` and reflection disabled.
It rejects missing shocks and overlapping seed splits.

Prepare a **copy** of `examples/experiments/scientific_s5_m1_s1.yaml`,
replacing `llm.model` with one actually available fixed model if you intend
to run live experiments. The placeholder string in the repository is not
a real-model guarantee. Then:

```bash
python -m research.civitas_agent.matrix \
  --template examples/experiments/scientific_s5_m1_s1.yaml \
  --output outputs/s6_plan \
  --dev-seeds 41,42 \
  --val-seeds 43,44 \
  --heldout-seeds 45,46,47,48,49
```

The command **creates configs only and makes ZERO model requests**. The
plan freezes 8 YAML files and `study_plan.json` containing seed partitions,
logical call budgets, expected arm identities, source scenario, named primary
metrics and pilot quality gates. Config SHA256 is checked before offline
execution and analysis. The held-out seeds are **independent synthetic
worlds**, not held-out historical market observations.

Primary metrics:
- mean absolute deviation from the *synthetic* fundamental benchmark;
- fraction of steps with executed trading;
- fraction of steps with valid two-sided exchange quotes.

Secondary: final benchmark deviation, realized volatility, traded volume,
average quoted depth, synthetic information reach. The pilot gates are:
at least 20% active steps, at least 10% two-sided quotes and at most 75% stale
price steps. These are provisional diagnostics (not empirical thresholds).

### API budget example

With 64 agents, a 25% cognitive cohort, 100 steps and LLM decision interval
10: 16 LLM investors × 10 decisions = **160 logical requests** per non-
reflection LLM arm and per independent world. B7 includes 4 reflection
opportunities × 16 agents, so **224 logical requests**. All five LLM arms
sum to **864 logical requests per seed**; nine seeds sum to **7,776 logical
requests**, excluding provider-side retries. Their monetary costs cannot be
computed without actual provider token usage and pricing.

This planning work does **not** run those calls. Do not submit the full
matrix to a paid API until a small, user-authorized pilot validates request
and token usage. Model response metadata must record actual provider, model
identity, usage and failures.

## 4. Execute deterministic controls first

```bash
python -m research.civitas_agent.run_rules \
  --plan outputs/s6_plan \
  --output outputs/s6_development_controls \
  --partition development

python -m research.civitas_agent.benchmark \
  --plan outputs/s6_plan \
  --runs outputs/s6_development_controls \
  --output outputs/s6_rule_scorecard \
  --partition development \
  --rule-only
```

This runs **only** B0/B1/B2, produces market and scientific traces, verifies
each archive, and makes **zero** model requests. The rule-only scorecard uses
B2 as the provisional reference; it should never be interpreted as the full
eight-arm comparison.

After the fixed LLM model is configured and explicit token budget is approved,
use the generated `configs/*.yaml` through the existing
`python main.py batch --config ... --seeds ...` command in a dedicated
output root. The same seed partitions must be preserved. Real provider
calls are **not** automatically launched by S6 tools.

Then, once all eight arms exist for the selected seed partition:

```bash
python -m research.civitas_agent.benchmark \
  --plan outputs/s6_plan \
  --runs outputs/s6_all_development_runs \
  --output outputs/s6_full_scorecard \
  --partition development
```

The scorecard verifies:
- Complete one-run-per-arm-per-seed design; frozen YAML SHA and full expanded
  configuration identity; matching world, population and cognitive-cohort
  snapshots; one source-code fingerprint across runs.
- Actual model-call records and expected logical model calls, with zero
  provider calls in deterministic arms.
- Market-quality gates, with explicit failure reasons.
- Within-seed paired differences against B6, world-level bootstrap
  intervals and no agent-step pseudo-replication.

With fewer than five independent worlds, it labels contrasts
`descriptive_only`. Passing a quality gate is **not** equivalent to
real-market calibration. Missing quotes do not mean zero spread. No
hypothesis is automatically accepted by this tool.

Held-out synthetic worlds require `--unlock-heldout` to read scores.
Never access held-out results while tuning the model and simulation settings.

## 5. Read-only external market-data reference validator

A separate CLI accepts **user-supplied, frozen, source-documented** CSV with
columns `date`, `close` and `volume`:

```bash
python -m research.civitas_agent.reference \
  --spec path/to/reference_spec.json \
  --output outputs/reference_development_report
```

`reference_spec.json` must contain EXACTLY:

```json
{
  "snapshot_path": "your_reference.csv",
  "expected_sha256": "64_lowercase_hex_characters_from_actual_file",
  "source_name": "Name of data vendor or archive",
  "source_url": "https://actual-public-source-or-documentation",
  "origin": "historical",
  "asset": "The exact asset or index identifier",
  "market": "The exchange and instrument class",
  "frequency": "daily",
  "price_adjustment": "adjusted",
  "timezone": "Asia/Shanghai",
  "development_end": "2023-12-31",
  "validation_end": "2024-12-31"
}
```

The example above is a **schema illustration**, not a real file or existing
historical dataset. The file SHA is checked, dates must be chronological and
unique at declared frequency, prices finite positive, volume nonnegative,
and development/validation/held-out partitions must be nonempty and have
sufficient observations.

The default reference report **does not compute held-out return statistics**.
The explicit `--unlock-heldout` flag is needed to release them after model
freeze. It reports log-return distribution properties, excess kurtosis,
absolute-return ACF and non-trading share. A file digest demonstrates file
identity, NOT independent source authenticity. The output retains
`historical_origin_independently_verified=false` until independently
checked. Synthetic test data is always labeled `synthetic_test`.

**Frequency caveat:** one simulator step does not automatically represent
one stock-exchange day or minute. A valid reference snapshot alone does not
prove simulated vs empirical fidelity, and OHLCV cannot validate LOB
spread/depth. Do not introduce real prices into the simulation through a
fixed-price or synthetic-trade workaround.

## 6. Verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_s6_baselines.py \
  tests/test_s6_matrix.py tests/test_s6_reference.py
python scripts/verify_minimal_experiment.py
```

Unit and CI tests use controlled synthetic fixtures and no paid provider
requests. The real-model pilot and empirical data integration remain
scientifically open. Next steps: independent counterfactual interpretation,
real-data source selection and licensing, finalized measurement plan,
sufficient world-level replications, compute-cost reporting and writing the
scientific Results section from **observed** rather than anticipated findings.

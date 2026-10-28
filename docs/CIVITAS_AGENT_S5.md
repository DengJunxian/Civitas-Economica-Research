# CivitasAgent S5 — Controlled scientific information world

**Status:** Implemented on stacked research branch. This is a **synthetic
mechanism-test environment**, not an empirically calibrated stock market or
evidence that any LLM agent behaves like an actual investor. It extends the
opt-in S0–S4 pilot, not the standard default simulation.

## 1. Research estimand and control structure

The system distinguishes three objects:

1. **Private latent fundamental** `V_t`: a researcher-specified benchmark. A
   truthful claim induces `V_t = V_(t-1) × (1 + polarity × magnitude)` at
   claim publication. False claims do not shift `V_t`. This benchmark never
   sets the exchange's transaction price.
2. **Claimed news content**: a synthetic information source announces a signed
   percentage estimate. **The identical claim** can be marked true or false
   in separate counterfactual worlds. Claim truth is never present in the
   model prompt until its scheduled public verification.
3. **Actual market transaction price** `P_t`: generated only by submitted
   limit orders, matching and the existing ClearingHouse.

The key descriptive mispricing outcome is
`absolute_price_deviation = abs(P_t - V_t) / V_t`. It is a synthetic
construct, not proof of *real-world* fundamental mispricing.

For each truth-labeled scenario, run the 2×2 memory × social factor
arms **with identical** seed, baseline population, model and market rules.
The S5 social factor controls the visibility and automatic relay of the
synthetic claim. For clean interpretation use `social_mode: news` to suppress
LLM-visible historical neighbor intentions; `social_mode: both` instead
makes both information channels visible and changes the estimand.

S5's native information diffusion is **deterministic**: seed a fixed
fraction of cognitive agents at the publication step, then relay to
immediate cognitive neighbors only one simulated step later. It is not a
model of LLM choosing whether to share news. Rule-based background traders
do not read these synthetic claims. Exposure coverage must be reported.

## 2. Temporal and provenance invariants

- A source claim has a stable `shock_id`, `source_id`, `step`,
  `reveal_step`, `polarity`, `magnitude`, `truthful`,
  `direct_fraction`.
- The runner `advance(step)` acts **before** investor observation and
  order submission. Only claims received at or before that step enter the
  personalized observation.
- Every exposure record specifies the source, actual receiving Agent, time,
  channel (direct/social) and forwarding Agent for social deliveries.
- `public_verifications` remains empty before `reveal_step`. Public
  verification and source track records use only **already revealed**
  verdicts. These are visible to everyone from the revelation step.
- Ground truth and `V_t` live in the simulator and in researcher-side
  artifacts, **never** the Agent observation. The simulator does not create
  a pre-reveal insider feed or condition investor actions on `truthful`.
- Post-settlement memory may incorporate **public** verifications after
  their scheduled step. Memory and social treatment assignment flags are
  not included in LLM prompts.
- The baseline LLM decision budget, malformed-schema fail-close, no silent
  provider fallback, and account invariants remain enforced.

**Security caveat:** `study_world_private.json` is a research artifact with
the researcher-held ground truth, not a credential-protected file. It is
*private from agents' input construction*, not protected from repository
readers. Never pass full run configs or researcher-side artifacts into the
Agent prompt.

## 3. Order arrival and market quality

`cognitive.order_schedule` supports `fixed` and `seeded_per_step`.
A seeded schedule uses a stable deterministic random permutation keyed by
world seed and step. Agent decisions are collected before arrival order is
applied, and the canonical decision archive remains ordered by Agent ID.
The exact arrival permutation is preserved in
`study_order_schedule.jsonl`. This is **arrival-order sensitivity
control**, not a guarantee that an LLM produces identical actions in two
separate real-model calls.

S5 appends these per-step values to `metrics.csv`:

- `fundamental_value`, `fundamental_return`
- `signed_price_deviation`, `absolute_price_deviation`
- `new_news_exposures`, `cumulative_news_exposures`,
  `cumulative_public_verifications`
- `two_sided_quote_valid`, `spread_pct_when_quoted`,
  `no_trade`, `stale_price`

`spread_pct_when_quoted` is **blank**, not zero, when there is no
valid two-sided quote. Summaries must report the quote coverage and
non-trading fraction alongside price deviation or volatility. A low
variance price without transactions does not imply good price discovery.

## 4. Archived evidence

Additional manifest-hashed S5 artifacts:

| File | Contents |
| --- | --- |
| `study_world_private.json` | synthetic scenario and true/false labels for researchers |
| `study_news_exposures.jsonl` | actual delivered claim timeline per investor |
| `study_news_verifications.jsonl` | public verification timeline |
| `study_fundamentals.csv` | the latent benchmark and price-error series |
| `study_order_schedule.jsonl` | actual order-arrival priority for each step |

Existing `cognitive_observations.jsonl` now includes
`information_signals` only when S5 is explicitly enabled.
`verify` checks archive integrity, expected row counts, and
no-future-information properties.

The offline paired-world analyzer additionally exports
`shock_windows.csv` with pre-event, unverified and post-verification
*descriptive* windows, plus S5 benchmark deviation and information reach
in `per_run.csv`, `paired_effects.csv`, and `effect_summary.csv`.
With one independent seed, intervals are **descriptive_only**.
Averages across overlapping events are **not** independently causal.

## 5. Experiment and validation commands

Install and execute offline tests:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_scientific_world.py tests/test_civitas_agent.py
python scripts/verify_minimal_experiment.py
```

**The test model is a controlled in-process fake.** None of those tests
use live LLM providers or produce empirical scientific results.

Example four-arm false-claim pilot:

```bash
# Edit llm.model in all four files to the same actually available model,
# configure the corresponding provider credentials, then validate:
python main.py validate --config examples/experiments/scientific_s5_m0_s0.yaml
python main.py validate --config examples/experiments/scientific_s5_m1_s0.yaml
python main.py validate --config examples/experiments/scientific_s5_m0_s1.yaml
python main.py validate --config examples/experiments/scientific_s5_m1_s1.yaml

# Explicit real API calls — NOT executed by the S5 code implementation.
python main.py batch \
  --config examples/experiments/scientific_s5_m0_s0.yaml \
  --config examples/experiments/scientific_s5_m1_s0.yaml \
  --config examples/experiments/scientific_s5_m0_s1.yaml \
  --config examples/experiments/scientific_s5_m1_s1.yaml \
  --seeds 41,42,43 \
  --output outputs/scientific_s5

python -m research.civitas_agent.analysis \
  --runs outputs/scientific_s5 \
  --output outputs/scientific_s5_analysis
```

Default example: 64 population, 16 cognitive investors, 100 steps, decision
every 10 steps = **160 logical model calls per run**, plus any explicitly
enabled reflection (off in examples). Four arms × three independent seeds =
**1,920 logical calls**, excluding provider retries. Obtain real usage/cost
before larger experiments. Examples use `replace-with-supported-model` as
a **non-working placeholder**, not a promise of availability.

## 6. Still needed before paper-grade results

1. **Independent scientific control checks:** compare true/false scenarios
   with identical claims; compare no-news placebo; confirm exposure and
   decision effects versus non-LLM rules.
2. **Empirical calibration:** external data with legal provenance, matching
   market frequency and a held-out evaluation split.
3. **Liquidity validity:** address persistent one-sided books, stale price
   paths and background trader policy, not by injecting fake trades.
4. **Mechanism isolation:** challenge deterministic relay, initial broadcast
   fractions, alternative network topologies and the rule-agent social
   coupling that remains active in every arm.
5. **S6 confirmatory protocol:** pre-registered primary outcomes, enough
   independent random worlds, effect sizes, uncertainty, provider stability
   and cost-aware baselines.
6. **Paper claims:** all findings must be produced by actual runs. Neither
   synthetic fake-model CI tests nor code completeness prove H1–H5.

See [S0–S4 protocol](CIVITAS_AGENT_STUDY.md) and the tracked S5–S7
research milestones in GitHub issue #3.

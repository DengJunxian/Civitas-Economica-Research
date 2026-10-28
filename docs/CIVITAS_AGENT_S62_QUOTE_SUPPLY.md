# S6.2: Account-backed passive-quote mechanism diagnosis

**Type:** controlled synthetic market **mechanism** experiment, not empirical
behavioral validation, not an equity market-making strategy, and not approval
to spend LLM credits. This project phase is stacked on S6.1, S6, S5 and S0–S4
research branches; main remains untouched.

## Motivation: the prior negative evidence

In S6.1 the paired development worlds (seeds 41 and 42, 64 agents,
60 steps) compared order lifetimes TTL=1,3,8. The result was
`NO_ACCEPTABLE_TTL_IN_DEVELOPMENT`. The market remained **ask-only**
for ~72–93% of steps. Mean resting bid depth was around 1–3 units,
whereas mean ask depth was hundreds to thousands of units. Simply keeping
old quotes alive did not create durable limit-order **buy** interest.

The primary hypothesis here is *narrower* than realistic market fidelity:
**A deliberately passive, inventory-constrained quoting policy can
mechanically improve end-of-step two-sided limit-order availability
relative to a matched-frequency random policy.**

## Intervention (transparent, account constrained)

In exactly the same fixed 25%-size cognitive cohort:

- **Random control:** uses the existing seeded random BUY/SELL/HOLD policy.
- **Passive-quote mechanism:** observes only its own cash/shares, last market
  price and top five visible order-book levels; chooses the thinner side
  (bid if asks dominate, ask if bids dominate, seeded side on ties).
- Quotes are limited to **5% of currently available cash or sellable shares**,
  and the existing adapter places the limit bid three price ticks *below
  the best ask* or the limit ask three ticks *above the best bid* when
  opposite quotes are available. If the chosen side is not funded, it
  switches to the funded side or HOLD.
- Every submitted order is a real order with a real participant account,
  subject to price/lot rules, resource reservation, cancellation, actual
  matching and double-entry asset settlement. No synthetic transactions,
  synthetic price movements, external cash, guaranteed fills, or
  privileged access to researcher-only fundamental truth.
- Both arms are given **one action opportunity per simulation step**.
  This is a deliberate matched-frequency change from the prior S6.1
  ten-step cognitive schedule. S6.1 and S6.2 figures must **not** be
  pooled as if they were a single-variable TTL experiment.

The rule is deliberately targeted at the measured problem. Improvements
would therefore be diagnostic proof of a missing *quote-supply mechanism*,
not evidence that real investors naturally behave this way.

## Factorial development study

| Factor | Arm |
| --- | --- |
| Quote behavior | `random` vs `passive_quote` |
| Order lifetime | TTL 1 vs TTL 3 |
| Development worlds | seed 41 and 42 |
| Total population | 64, with 25% cognitive and 75% existing vectorized background traders |
| Duration | 40 model steps per world |
| News/events | None |
| LLM/API calls | **0** |

Total **8** complete account-cleared runs. This is development-only and
inference remains descriptive with two independent worlds.

Measure both-sided, one-sided and empty-book fractions, conditional spread,
bid/ask depth, trade step coverage, stale prices, actual volume, order
rejections and TTL cancellations; verify every manifest and archived
cash+fee/share conservation. Same seed initial world, population, agent
cohort and arrival schedule must match across arms. The *only* intended
per-pair change at fixed TTL is the decision policy.

The exploratory per-world guardrail requires the passive-quote mechanism to
(1) pass the inherited S6 market quality gates (10% bid/ask, 20% active
steps, <=75% stale price), (2) improve quote coverage over random,
(3) lower trading activity by no more than 10 percentage points, and
(4) increase the order rejection fraction by no more than 10 points.
A provisional mechanism candidate must pass all guardrails across both
development worlds. This is a transparent engineering threshold, not a
calibrated financial benchmark.

A passing development candidate still has
`paid_llm_ready = false`,
`market_realism_verified = false`. It needs independent validation,
quantitative checks of bid/ask quote sizes and aggressiveness, and
later external market-data calibration.

## Commands

```bash
# Tiny implementation smoke, 12 accounts × 8 steps, two TTL values, one seed
python -m research.civitas_agent.quote_supply \
  --ttls 1,3 --seeds 41 --agents 12 --steps 8 \
  --output outputs/s62-quote-smoke

# Full offline development evidence (no credentials required)
python -m research.civitas_agent.quote_supply \
  --ttls 1,3 --seeds 41,42 --agents 64 --steps 40 \
  --output outputs/s62-quote-development
```

Outputs:
- `study_design.json` and each run's verified experiment manifest;
- `quote_supply_per_run.csv` and `paired_policy_differences.csv`;
- `policy_guardrails_by_world.csv`, `policy_effect_summary.csv`;
- `decision.json` with explicit no-paid-LLM flags.

The GitHub Actions job uploads raw transaction/account state evidence and
derived comparisons. No hypothesis is accepted from an engineering smoke.
Even if there are enough standing quotes, true market realism requires
independent data and empirically realistic quoting behavior.

## Do not do

Do not insert price-improvement routines to override the executed market
price, increase investor cash without updating the full conservation
identity, disable account-reservation checks, or call a fake quote a real
trade. Do not state an observed experimental result before CI run artifacts
exist. Do not tune on held-out worlds.

# S6.1: Liquidity Diagnosis Before Paid LLM Experiments

**Status:** offline controlled engineering study. Not a scientific proof of
financial-market realism; not a user-trading recommendation; no LLM/API
requests. The S0–S6 research branches remain separate from `main`.

## Why this stage is necessary

The earlier [S6 full-size offline run](CIVITAS_AGENT_S6_OFFLINE_FINDINGS.md)
used one 64-investor/100-step synthetic market, seed 41 and three rule
investor baselines. Every arm FAILED the predetermined two-sided quote
coverage gate: 5%, 4%, and 4% versus the minimum 10%. It is inappropriate
to launch large paid LLM tests on that exact microstructure configuration.

The current core runner expires orders as follows: each order is given its
submission step and is canceled at the beginning of a subsequent step once
`current_step - submission_step >= market.order_ttl`. The prior default
`order_ttl=1` therefore removes every unfilled order at the start of the
following step. This mechanism plausibly contributes to a thin book, but it
does NOT prove that simply lengthening order life fixes market quality.

## Predeclared S6.1 development contrast

**Intervention:** change ONLY the limit order time-to-live, comparing TTL
values `1, 3, 8`, before selecting a candidate. All runs use the *same*
synthetic no-news source configuration and the same controlled market:

- 64 total investors, 100% with real accounts, actual limit orders,
  Python matching and ClearingHouse settlement.
- A fixed 25% cognitive cohort executes the transparent B0 random policy
  every 10 market steps. The other 75% use the existing vectorized trader.
  Nobody calls an LLM, and no researcher-truth information is shown.
- Random order-arrival permutation is seeded by world + time step, with
  the same ordering in every TTL arm.
- **No news shock or inferred fundamental-value change** during the TTL
  development study. The latent benchmark remains an internal measurement
  reference only.
- Start with development-only seeds 41 and 42, 64 agents, 60 steps. These
  are not model-validation/held-out seeds. This trial is intentionally
  descriptive: two independent worlds are insufficient to claim
  significance or choose a globally optimal setting.

The core model rules are not modified: no artificial buy/sell transactions,
no liquidity injection, no direct editing of market prices, no overdrafts,
no shorting, no removal of settlement invariants.

## Measurements and guards

Each run is verified from the manifest, and the archived account series is
independently checked for total-share and cash-plus-fee conservation.

Market measures are end-of-step observations from actual order-book depth:

| Measure | Interpretation |
| --- | --- |
| `two_sided_quote_share` | Fraction of steps with positive resting bid AND ask |
| `bid_only_share`, `ask_only_share`, `empty_book_share` | Exhaustive partition with two-sided state |
| `mean_spread_pct_when_quoted` | Spread **only** on valid two-sided quote steps (otherwise NA) |
| `active_step_share`, `stale_price_share` | Trading activity and stale transaction price |
| `mean_bid_depth`, `mean_ask_depth` | Actual observed resting quantities |
| `order_rejection_share` | Final rejected orders / recorded orders |
| `ttl_cancellation_share` | TTL cancellation events / recorded orders |
| `total_volume` | Real matched transaction quantity |

**Pilot gates (unchanged from S6):**
- at least 10% valid two-sided quoted steps;
- at least 20% steps with actual trading;
- no more than 75% stale-price steps.

A candidate TTL must pass **all gates in every development seed**.
Select the smallest qualifying TTL (not the one with the prettiest
price chart). This is a provisional *development* result only, and
`paid_llm_ready` remains `false` even if a TTL qualifies.

To interpret price discovery, additionally inspect order rejection,
cancellation intensity, spread conditional on bilateral quotes, and
whether trading activity collapses. A higher fraction of stale,
uncompetitive resting quotes is not evidence of useful liquidity.

## Execution

The predefined offline development sweep (NO keys required) is:

```bash
python -m research.civitas_agent.liquidity_sweep \
  --template examples/experiments/scientific_s5_no_claim_m1_s1.yaml \
  --ttls 1,3,8 --seeds 41,42 --agents 64 --steps 60 \
  --output outputs/s61-ttl-development
```

Outputs include six full run archives, paired seed-level differences,
`liquidity_per_run.csv`, `ttl_effect_summary.csv`,
`ttl_quality_summary.csv`, `sweep_plan.json`, and
`decision.json`. The archive includes both successes and explicit gate
failures; it will never report a paid-LLM go decision.

An abbreviated 12-investor/8-step two-TTL CI smoke tests only that the
program executes. It is **not** a substitute for the 64-investor sweep.

## Stop / go and future study

- If **no** TTL passes, STOP: do not spend LLM tokens. Investigate order
  pricing, lifetime, account resource reservation and actual bilateral
  counterparty supply without manufacturing trades.
- If a TTL passes, test that frozen candidate and TTL1 in the **separate
  validation seeds** with B0/B1/B2, while keeping all other parameters
  fixed. Do not inspect held-out test seeds.
- Only after independent validation, sufficient actual double-sided
  depth, acceptable execution activity, review of transaction costs, and
  explicit user authorization should a small real-model B3–B7 pilot run.

### Causal limit

The sensitivity design estimates the effect of TTL **inside the specified
synthetic simulator**, conditional on its initial population and trader
policies. It cannot identify how changing order lifetime would affect
real A-share liquidity. The latest real external market data validation
remains open in S6; a good CI result is not a real-data result.

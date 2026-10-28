# S6.1: Observed paired TTL sweep — no candidate passes

**Evidence source:** GitHub Actions [run 40690613238](https://github.com/DengJunxian/Civitas-Economica-Research/actions/runs/40690613238), artifact [s61-liquidity-development, ID 12817401604](https://github.com/DengJunxian/Civitas-Economica-Research/actions/runs/40690613238/artifacts/12817401604).

**Exact protocol:** two DEVELOPMENT-ONLY synthetic worlds (seeds 41 and 42), 64 total investors, 60 discrete market steps per world, 25% cognitive investors using the non-LLM seeded random policy at ten-step intervals and the remaining 75% using the existing vectorized trading rule. No news shocks, no API calls, actual OrderBook/ClearingHouse, all stored accounts and trades hash-verified. The only experimental variable is the outstanding order's lifetime in simulation steps: TTL 1, 3 or 8. Total **6 complete, verified runs**. This is a deterministic engineering diagnostic, NOT empirical finance evidence or a statistically powered causal study.

## 1. Primary market results

| Seed | TTL | Two-sided quote share | Trading-active step share | Stale-price step share | Bid-only share | Ask-only share | Pass 10% two-sided gate? |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 41 | 1 | 8.33% | 56.67% | 43.33% | 0% | 91.67% | **NO** |
| 41 | 3 | 6.67% | 51.67% | 51.67% | 0% | 93.33% | **NO** |
| 41 | 8 | 6.67% | 50.00% | 50.00% | 0% | 93.33% | **NO** |
| 42 | 1 | 16.67% | 70.00% | 30.00% | 0% | 83.33% | yes |
| 42 | 3 | 20.00% | 56.67% | 45.00% | 0% | 80.00% | yes |
| 42 | 8 | 28.33% | 51.67% | 48.33% | 0% | 71.67% | yes |

**Actual selection output:** `candidate_ttl = null`,
`development_status = NO_ACCEPTABLE_TTL_IN_DEVELOPMENT`,
`paid_llm_ready = false`. No variant clears the predefined gate across
**both** development worlds. Hold-out worlds were untouched.

### Direct structural observation

The market is strongly **ask-dominated**. All six runs have zero bid-only
end-of-step observations; when a book is not two-sided, it is ask-only
rather than empty. Across the six runs the mean resting bid depth
ranges from roughly **0.97 to 2.78 asset units**, while the mean resting
ask depth ranges from roughly **465.6 to 1,903.5 units**. This gap is
too large to treat as a minor spread-quoting issue. The "two-sided quotes"
that do exist have conditional spreads approximately **0.035%–0.058%**,
but so few valid quote snapshots exist that the attractive conditional
spreads cannot establish overall liquidity quality.

### Why merely extending TTL is inadequate

- In seed 42, quote coverage increases from 16.67% at TTL1 to 28.33% at
  TTL8, but trading-active steps drop from 70% to 51.67%.
- In seed 41, quote coverage actually decreases from 8.33% at TTL1 to
  6.67% at TTL8; trading-active steps decrease from 56.67% to 50%.
- Roughly 18%–22% of orders end rejected across the six runs, and about
  66%–73% of recorded orders generate TTL cancellations. Exact per-run
  counts and reasons are in the archived CSVs; **these are not rates
  per unit real time**.
- Holding orders longer without improving how **bid-side limit demand**
  is generated does not consistently produce a bilateral limit order book.

Only 2 independent worlds were evaluated, so reported differences are
**descriptive** with no defensible significance claims. The earlier
64-agent, 100-step S6 one-world negative pilot used a different horizon:
these numerical quote percentages must not be compared as if the
observation windows were identical.

## 2. Stop condition and next falsifiable test

**Stop:** do NOT promote TTL3/TTL8 to the default market, do NOT report
market fidelity and do NOT launch paid B3–B7 LLM experiments on this
configuration.

The next **separate exploratory intervention** should modify the
*microstructure supply mechanism*, while preserving cash/share settlement:
introduce a transparent passive quoting behavior among a fixed number
of normal, endowment-constrained Agents. Those Agents can submit
genuine non-crossing bids or asks using their own cash and inventory;
they must have no access to researcher-only fundamental truth, and
their orders must be cancellable, rejectable and fillable in the same
OrderBook. Compare with a matched-frequency rule baseline using the
same seeds, population, and submission ordering.

That intervention is deliberately designed to improve quote coverage,
so improvement alone would NOT be proof of investor realism. Report
price-impact, actual fills, cash reservation, quote aging and any
deterioration in activity along with bilateral depth.

If that study fails, improve the information and behavior specification,
not by fixing prices or injecting fake trades. Independent validation and
external market-data calibration are required before any paper-level
claims.

**Data origin:** controlled synthetic model data only, not real investors.

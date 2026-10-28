# S6.2: Observed quote-supply mechanism pilot — improvement not robust

**Evidence:** initial eight-world endowment-constrained passive quoting experiment,
GitHub Actions [run 40692100836](https://github.com/DengJunxian/Civitas-Economica-Research/actions/runs/40692100836), archived artifact
[ID 12817922116](https://github.com/DengJunxian/Civitas-Economica-Research/actions/runs/40692100836/artifacts/12817922116).

**Data:** 64 agents, 40 simulated steps, DEVELOPMENT seeds 41/42, no news,
same initial account endowments and cohort, 25% cognitive quote or random
traders given **equal once-per-step opportunities**, remaining 75% existing
vectorized background traders. Factorial arms: Random vs PassiveQuote at
TTL1 and TTL3. **8 verified scientific-synthetic engineering runs; zero
live model/API calls.** This is neither historical financial data nor
a statistically powered human-behavior study.

## A. Actual quote availability and trading

| Seed | TTL | Random two-sided quotes | PassiveQuote two-sided quotes | Passive minus random | Active step change |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 41 | 1 | 10.0% | 32.5% | **+22.5 pp** | **−20.0 pp** |
| 41 | 3 | 12.5% | 32.5% | **+20.0 pp** | **−12.5 pp** |
| 42 | 1 | 22.5% | 32.5% | **+10.0 pp** | −5.0 pp |
| 42 | 3 | 22.5% | 20.0% | **−2.5 pp** | −7.5 pp |

At TTL=1 seed 41, a targeted true, account-backed passive-quote policy
raised bilateral quote coverage from 10% to 32.5%, but reduced the
share of steps with an actual trade from **100% to 80%**. At TTL=3,
seed 42, passive quoting reduced quote coverage. **An improvement in
some synthetic-market snapshots is not a universal microstructure fix.**

The two predeclared side-effect guardrails require the passive supplier to
lower active trading by no more than 10 percentage points, and to increase
rejections by no more than 10 points. Only **seed 42 / TTL1** passed the
entire exploratory mechanism guardrail. No TTL passed for every seed.
`candidate_ttl_with_passive_quotes = null`,
`QUOTE_SUPPLY_MECHANISM_DID_NOT_CLEAR_ALL_DEV_GUARDS`,
`paid_llm_ready = false`. Held-out test worlds remain untouched.

The bid side remains tiny relative to ask-side depth even in passively
quoted worlds. For example seed41/TTL1 mean end-of-step bid depth increased
from **1.525 to 4.000 units**, while the corresponding ask depth was
**1,721 to 1,871 units**. Bidirectional quote-presence does not imply a
balanced depth profile or meaningful price discovery.

All numbers are from two independent synthetic worlds; no significance
claim is possible. Passive quoting is purposefully engineered for the
target metric, so positive coverage changes do NOT validate real-world
market-maker behavior or causally show that genuine investors act that way.

## B. Independent root-cause audit: directional behavior is severely skewed

I independently inspected the earlier **S6.1 seed 41, TTL1** archived
`decisions.csv` and `orders.csv` (the 64-Agent ×60-Step no-news
development world):

- 3,840 individual actions: **2,338 SELL**, **176 BUY**, **1,326 HOLD**;
  buy decisions ~4.6%, sell decisions ~60.9%.
- Requested total sell quantity **873,040 units**, requested total
  buy quantity **1,080 units**.
- The first-order imbalance exists in the **agent decision output**, before
  exchange matching or settlement, and is amplified by differing
  proposed order sizes.
- In this specific no-news trajectory, the executed price falls from
  3,000 toward the fixed **10%-below-initial** daily price band near
  **2,700**, after which large numbers of proposed sell limit prices
  fall outside the permitted band. This is consistent with market
  pressure and the daily-band constraint interacting.
- In the archived TTL1 run, **374 rejected orders had no recorded
  rejection reason** (alongside 131 explicitly marked insufficient
  sellable holdings). Code inspection identified missing `reason`
  assignment for out-of-band limit orders and symbol mismatch.
  The S6.2 research branch fixes those logging omissions and adds
  regression tests. **This metadata correction does not alter order
  matching or price formation**.

### Mechanism hypothesis — not yet causally established

The existing `agents/population.py` behavioral pipeline:
- computes Prospect Theory-like negative utility and
  `direction = tanh(utility*8)`;
- combines this with sentiment into `trading_intent`, then uses
  nonlinear buy/sell probabilities; and
- sizes buys from a fraction of available cash divided by price,
  whereas sells are sized from 40–100% of available holdings.

These design choices are plausible contributors to a sell-pressure
feedback loop after falling prices. But the observed counts alone cannot
separately attribute the sell bias to reference points, loss aversion,
initial inventory, sentiment, position sizing, the social-network model,
or the price-limit regime.

**Next falsifiable stage (S6.3):** measure/action attribution of buy/sell
probability, intended/accepted/rejected quantity and reserve balances
as functions of initial reference anchors and price path; use explicit
ablation or frozen-input counterfactuals of reference sensitivity and
size scaling, while preserving order-book and account constraints.
Ensure no calibration against held-out worlds.

## C. Decision

The economic claims H1–H5 remain unproven. The full paid LLM matrix
B3–B7 should **not** launch on an unvalidated market with strongly
asymmetric buy/sell behavior and inadequate bilateral depth. Continue
mechanism debugging, independently verify multiple development worlds,
then freeze a model specification for empirical market-data comparison.

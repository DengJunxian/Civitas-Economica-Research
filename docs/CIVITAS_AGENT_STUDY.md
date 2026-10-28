# CivitasAgent: auditable memory × social LLM pilot (S0–S4)

**Status:** opt-in research implementation on a branch. This is **not** a
scientific validation of LLM investor realism, policy effectiveness, or market
calibration. No live provider experiments or held-out market comparisons have
been completed by adding this code.

## Research question and pre-registered contrasts

Investigate whether an investor's **own post-settlement episodic memory**
changes the market response to **previous-step neighbor intention broadcasts**.
Start with the four paired conditions:

| Arm | Memory visible? | Neighbor intentions visible? |
| --- | --- | --- |
| M0/S0 | no | no |
| M1/S0 | yes | no |
| M0/S1 | no | yes |
| M1/S1 | yes | yes |

The joint contrast is Y11 − Y10 − Y01 + Y00, evaluated across paired independent
simulation worlds. This initial treatment is **not** a truth-labeled news
contagion model. Reflection is disabled in the four primary arms and should be
evaluated in a separate ablation.

### What the new code actually does

- Reuses the existing **standard ExperimentRunner**, validated limit-order
  book and ClearingHouse. The default vectorized, profile heuristic, mock
  and legacy LLM pathways are unchanged in intended trading semantics.
- Samples a stable cognitive cohort from the seed and `llm_share`. The other
  agents run the existing vectorized behavior at every exchange step.
- The cognitive cohort calls the model only at steps
  `1, 1 + decision_interval, ...`. Between calls it does not blindly
  resubmit stale orders.
- A cognitive observation contains public price / limited book depth, the
  investor's own portfolio and persona, past price history, optional own
  episodic memory and optional **previous-step neighbor intentions**.
- A strict JSON intent is converted to an exchange-limited, lot-quantized
  order subject to the actual available cash / shares. A malformed model
  response terminates the research run and preserves failure artifacts.
- Episodic entries are written only *after* the market step's settlement.
  Social intentions are recorded for all participants after each step and
  become observable by neighbors only at later steps. They report **intent**
  rather than fills; do not describe them as observed private trades.
- Optional LLM reflection produces a short retrospective summary based on
  prior recorded outcomes. It is disabled by default, charged to the model
  call budget and not visible until a later observation.
- Writes six additional auditable artifacts:
  `cognitive_observations.jsonl`, `cognitive_decisions.jsonl`,
  `cognitive_outcomes.jsonl`, `cognitive_social_messages.jsonl`,
  `cognitive_reflections.jsonl`, `cognitive_protocol.json`.
  They are covered by manifest hashes and archive verification.
- Source fingerprints include the research Python modules. Live LLM studies
  cannot use deterministic `reproduce`; reruns must be explicit new experiments.

### Scientific and architectural limitations

1. **Not an EconAgent replication**: this is a bounded experiment protocol for
   a single-asset financial market with cash-and-share settlement.
2. **No genuine news source credibility yet**: neighbor messages contain
   previous intent direction only. Source identity is the neighboring agent.
   Truth-labeled rumors, evidence gating and fundamentals are future tasks.
3. **Not real-world calibrated**: existing synthetic personas and rules are
   model assumptions, not validated investor microdata. Historical input in
   the standard runner initializes price only.
4. **No market-maker control or external fundamental value** is added.
   Zero-trade bars carry forward the last transaction price. Check both-sided
   quote rates and liquidity before interpreting volatility.
5. **LLM calls are not bitwise deterministic**, even with temperature zero.
   The model/provider is explicitly set and usage is logged by the LLM router.
   Prompt/observation snapshots are in the experiment archive. Provider
   service revisions may alter outcomes.
6. **Background rule traders still interact through the existing simulation
   network** in every arm. The S factor only turns *LLM-visible neighbor intent*
   on or off; it does not remove all social effects in the whole world.
7. The conversion of fractional preferences to limit orders is a modeling
   assumption. Compare it to a direct-order baseline before claiming
   behavioral fidelity.

## Offline validation

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_civitas_agent.py tests/test_experiments.py
python scripts/verify_minimal_experiment.py
```

The cognitive tests use a controlled in-process LLM client and **never**
make real provider calls. They verify all four arms, same seeded cognitive
cohort, prior-step visibility, post-settlement memory, reflection timing,
budget/schema validation, artifact verification, failure archiving and
the non-deterministic replay guard.

## Live paired pilot (explicit paid API usage)

Install the optional LLM dependencies and set the provider's own credential
environment variable. Edit each YAML's `llm.model` from the placeholder
to an actually available fixed model. Revalidate provider settings/price
before any large run.

```bash
python -m pip install -r requirements-core.txt -r requirements-llm.txt
python main.py validate --config examples/experiments/civitas_agent_m0_s0.yaml
python main.py validate --config examples/experiments/civitas_agent_m1_s0.yaml
python main.py validate --config examples/experiments/civitas_agent_m0_s1.yaml
python main.py validate --config examples/experiments/civitas_agent_m1_s1.yaml
python main.py batch \
  --config examples/experiments/civitas_agent_m0_s0.yaml \
  --config examples/experiments/civitas_agent_m1_s0.yaml \
  --config examples/experiments/civitas_agent_m0_s1.yaml \
  --config examples/experiments/civitas_agent_m1_s1.yaml \
  --seeds 41,42,43
```

Pilot cost calculation: 64 agents × 0.25 = 16 cognitive investors;
steps 1,11,...,91 give 10 decisions per cognitive investor, so **160 logical
requests / arm / seed**, before provider-level retries. The four arms and
three seeds require **1,920 logical calls** in total. This does not estimate
token charges, which must be measured from `model_calls.json` before scaling.
Each run archives its own final model response metadata.

## Required next-stage work (not implemented in S0–S4)

- **S5:** exogenous latent fundamental value, truth-labeled shock generator,
  explicit information provenance, exposure and belief-update protocol,
  exchange-order scheduling controls, and order-book availability diagnostics.
- **S6:** named baselines and full factorial experiment harness with
  independent world-level replication, pre-registered metrics, confidence
  intervals, no-lookahead external calibration/held-out data boundaries.
- **S7:** longitudinal research figures and scientific manuscript, including
  negative results, failure cases and model/compute-cost comparison.

Do not report the controlled test fixtures or the presence of model/market
modules as evidence of the paper's behavioral or market-level hypotheses.

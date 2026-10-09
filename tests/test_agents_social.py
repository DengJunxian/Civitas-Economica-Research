import hashlib
import random

import numpy as np
import pytest

from agents.persona import PersonaGenerator
from agents.population import StratifiedPopulation
from core.society.network import SocialGraph, InformationDiffusion


def test_persona_reproducibility_and_population_baseline():
    a = PersonaGenerator.generate_distribution(12, seed=17)
    b = PersonaGenerator.generate_distribution(12, seed=17)
    assert [p.to_dict() for p in a] == [p.to_dict() for p in b]
    assert len({p.archetype_key for p in a}) > 1
    assert all(0 <= p.conformity <= 1 and p.loss_aversion > 0 for p in a)
    p = StratifiedPopulation(n_smart=0, n_vectorized=12, seed=42)
    assert hashlib.sha256(p.state.tobytes()).hexdigest() == "65568a0643d31d28d430b854e21631b855717021c7dae142fa5bfd5bd8717bf9"


def test_population_owns_random_stream():
    a = StratifiedPopulation(n_smart=0, n_vectorized=12, seed=42)
    b = StratifiedPopulation(n_smart=0, n_vectorized=12, seed=42)
    random.seed(999); np.random.seed(999)
    ar = a.generate_tier2_decisions(3000)
    random.random(); np.random.random(30)
    br = b.generate_tier2_decisions(3000)
    for x, y in zip(ar, br):
        np.testing.assert_array_equal(x, y)
    assert set(ar[0]).issubset({-1, 0, 1})
    assert np.all(ar[1] >= 0)


def test_single_agent_neighbors_and_invalid_counts():
    p = StratifiedPopulation(n_smart=0, n_vectorized=1, seed=1)
    p.update_tier2_sentiment([], 0)
    assert p.state.shape == (1, 6)
    with pytest.raises(ValueError):
        StratifiedPopulation(n_smart=-1)


def test_sirs_is_repeatable_and_state_count_conserved():
    a = SocialGraph(20, 4, 0.2, seed=1)
    random.seed(40)
    b = SocialGraph(20, 4, 0.2, seed=1)
    assert list(a.graph.edges()) == list(b.graph.edges())
    assert [n.focus_topics for n in a.agents.values()] == [n.focus_topics for n in b.agents.values()]
    da, db = InformationDiffusion(a), InformationDiffusion(b)
    assert da.inject_panic(4) == db.inject_panic(4)
    for _ in range(15):
        x = da.update_sentiment_propagation()
        random.random()
        y = db.update_sentiment_propagation()
        assert x == y
        assert x["susceptible"] + x["infected"] + x["recovered"] + x["bullish"] == 20


@pytest.mark.parametrize("n,k,p", [(0, 0, 0), (4, 4, 0), (5, 3, 0), (5, 2, 2)])
def test_invalid_graph(n,k,p):
    with pytest.raises(ValueError):
        SocialGraph(n,k,p)

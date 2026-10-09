"""Lazy public exports; importing a persona does not load LLM/UI dependencies."""
from importlib import import_module

_EXPORTS = {
    "ExecutionIntent": "agents.manager_agent", "ManagerAgent": "agents.manager_agent",
    "InvestmentHorizon": "agents.persona", "Persona": "agents.persona",
    "PersonaGenerator": "agents.persona", "RiskAppetite": "agents.persona",
    "AgentProtocol": "agents.population", "PopulationEngine": "agents.population",
    "SmartAgent": "agents.population", "StratifiedPopulation": "agents.population",
    "TraderAgent": "agents.trader_agent",
}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value

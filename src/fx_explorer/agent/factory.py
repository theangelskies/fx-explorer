"""Builds the real model and the Agent from settings (used by the CLI and the API)."""

from __future__ import annotations

from fx_explorer.agent.loop import Agent
from fx_explorer.agent.model import AnthropicModel, Model, ModelError, OpenRouterModel, RuleBasedFakeModel
from fx_explorer.agent.prompts import CurrencyContext
from fx_explorer.agent.tools import ToolBox
from fx_explorer.config import Settings

FAKE_CURRENCIES = "  - USD (US Dollar), latest stored date 2026-11-17\n  - GBP (British Pound), latest stored date 2026-11-17"


def build_model(settings: Settings, dev: bool = False) -> Model:
    """The language model chosen by AGENT_PROVIDER. Raises ModelError if its key is missing."""
    name = settings.agent_dev_model if dev else settings.agent_model
    if settings.agent_provider == "openrouter":
        key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        return OpenRouterModel(
            model=name, api_key=key, base_url=settings.openrouter_base_url, max_retries=settings.max_retries
        )
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return AnthropicModel(model=name, api_key=key, max_retries=settings.max_retries)


def build_agent(settings: Settings) -> Agent:
    """An Agent for the API. With AGENT_FAKE=1 it uses the rule-based fake model."""
    toolbox = ToolBox(settings.agent_api_base_url, settings.http_timeout_seconds)
    if settings.agent_fake:
        currencies = CurrencyContext(toolbox)
        currencies._text, currencies._fetched_at = FAKE_CURRENCIES, float("inf")  # never fetched
        return Agent(RuleBasedFakeModel(), toolbox, settings.agent_max_steps, currencies)
    try:
        model = build_model(settings)
    except ModelError:
        toolbox.close()
        raise
    return Agent(model, toolbox, settings.agent_max_steps)

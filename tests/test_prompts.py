import pytest

from app.llm import prompts

NAMES = ["SUPERVISOR_SYSTEM", "RESEARCH_RANK_SYSTEM", "ITINERARY_SYSTEM", "EDIT_ROUTER_SYSTEM",
         "BASELINE_AGENT_SYSTEM"]


@pytest.mark.parametrize("name", NAMES)
def test_prompt_has_grounding_clause_and_is_short(name):
    text = getattr(prompts, name)
    assert text.rstrip().endswith(prompts.GROUNDING)
    assert len(text) < 1200, f"{name} is {len(text)} chars"

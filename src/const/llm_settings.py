"""모델을 바꾸지 않고 환경별 추론량·출력량을 제어합니다."""

import os
import re


def llm_settings(model: str, purpose: str = "llm") -> dict:
    settings = {}
    prefix = purpose.upper()
    effort = os.getenv(prefix + "_REASONING_EFFORT") or os.getenv("LLM_REASONING_EFFORT")
    # minimal은 원래 GPT-5 제품군에만 적용합니다.
    if effort and re.fullmatch(r"gpt-5(?:-nano|-mini)?(?:-\d{4}-\d{2}-\d{2})?", model):
        settings["reasoning_effort"] = effort
    budget = os.getenv(prefix + "_MAX_OUTPUT_TOKENS")
    if budget:
        settings["max_tokens"] = int(budget)
    return settings

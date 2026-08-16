"""LLM 抽象：统一生成接口，支持 OpenAI 兼容端点；无 key 时用规则模板回退。"""
from __future__ import annotations

import os
from typing import Protocol


class ChatModel(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 2000, temperature: float = 0.3) -> str:
        ...


class OpenAICompatibleClient:
    """兼容 OpenAI SDK 的客户端，可指向任意 openai 兼容 base_url。"""

    def __init__(self, api_key: str, base_url: str | None = None, model: str = "deepseek-chat"):
        from openai import OpenAI  # type: ignore

        kwargs: dict = {"api_key": api_key, "model": model}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model

    def generate(self, prompt: str, *, max_tokens: int = 2000, temperature: float = 0.3) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""


class RuleBasedReport:
    """无 LLM 时的确定性回退：生成简单的文本研报。"""

    def generate(self, prompt: str, *, max_tokens: int = 2000, temperature: float = 0.3) -> str:
        # 由 orchestrator 传入结构化内容，这里仅包裹一个说明头
        head = "[离线模式 · 未配置LLM，以下为数据驱动的结构化结论，可配置API key升级为自然语言研报]\n\n"
        return head + prompt


def build_chat_model(settings) -> ChatModel:
    """按配置构建模型。未配置 key 时回退到规则报告。"""
    api_key = settings.llm_api_key or os.getenv("LLM_API_KEY")
    if api_key:
        try:
            return OpenAICompatibleClient(
                api_key=api_key,
                base_url=settings.llm_base_url or None,
                model=settings.llm_model,
            )
        except Exception:  # noqa: BLE001
            return RuleBasedReport()
    return RuleBasedReport()

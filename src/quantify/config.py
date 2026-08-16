"""配置加载与访问。

优先级：环境变量(QUANTIFY_*) > config/settings.yaml > pydantic 默认值。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = ROOT_DIR / "config" / "settings.yaml"


class AppConfig(BaseModel):
    name: str = "quantify-agent"
    env: str = "dev"
    data_dir: str = "./data/cache"


class DataConfig(BaseModel):
    provider: str = "akshare"
    cache_ttl_seconds: int = 3600
    default_market: str = "A"
    asharpe_index: str = "000300"


class ValuationConfig(BaseModel):
    risk_free_rate: float = 0.022
    equity_risk_premium: float = 0.06
    prefer_low_pe: bool = True
    prefer_low_pb: bool = True
    pe_band: list[float] = Field(default_factory=lambda: [8.0, 25.0])
    pb_band: list[float] = Field(default_factory=lambda: [1.0, 6.0])


class PortfolioConfig(BaseModel):
    max_position_pct: float = 0.20
    max_single_industry_pct: float = 0.30
    target_drawdown_limit: float = 0.15
    cash_buffer_min: float = 0.05


class KnowledgeConfig(BaseModel):
    rules_dir: str = "quantify/knowledge/rules"


class LLMConfig(BaseModel):
    enabled: bool = True
    max_tokens: int = 4000
    temperature: float = 0.3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUANTIFY_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = AppConfig()
    data: DataConfig = DataConfig()
    valuation: ValuationConfig = ValuationConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    llm: LLMConfig = LLMConfig()

    # 通过环境变量直接注入的字段（不带 QUANTIFY_ 前缀，用于第三方密钥）
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: str = "deepseek-chat"
    tushare_token: Optional[str] = None


@lru_cache
def load_settings(settings_file: Path = DEFAULT_SETTINGS) -> Settings:
    """加载 settings.yaml 覆盖到默认配置上（文件优先于默认值）。"""
    data: dict = {}
    if settings_file.exists():
        data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
    # 展平顶层键：yaml 里的 app/data/... 已含在其下，直接用整个 dict
    return Settings(**data)


def get_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
    s = load_settings()
    # 从 .env / process env 读取第三方密钥
    s.llm_api_key = s.llm_api_key or os.getenv("LLM_API_KEY")
    s.llm_base_url = os.getenv("LLM_BASE_URL")
    s.llm_model = os.getenv("LLM_MODEL", s.llm_model)
    s.tushare_token = s.tushare_token or os.getenv("TUSHARE_TOKEN")
    return s

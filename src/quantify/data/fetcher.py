"""数据获取器：把第三方数据源(aakshare等)映射为领域模型。

为便于开发和离线验证，提供 LocalProvider 作为确定性回退源；
接口一致，切换 provider 无需改动下游代码。
"""
from __future__ import annotations

from datetime import date

from quantify.config import Settings
from quantify.data.cache import DiskCache
from quantify.data.schema import Financials, Quote, StockBundle


class FetcherError(RuntimeError):
    pass


class BaseProvider:
    """数据提供方接口。所有方法应返回领域模型。"""

    def fetch_quote(self, symbol: str, market: str = "A") -> Quote:
        raise NotImplementedError

    def fetch_financials(self, symbol: str, market: str = "A") -> Financials:
        raise NotImplementedError

    def fetch_bundle(self, symbol: str, market: str = "A") -> StockBundle:
        return StockBundle(
            quote=self.fetch_quote(symbol, market),
            financials=self.fetch_financials(symbol, market),
        )


class LocalProvider(BaseProvider):
    """本地确定性回退源，用于离线开发、测试与演示。

    用内置的少量"模拟财报"数据填充虚构标的，保证管道可端到端跑通。
    """

    _DEMO = {
        "600519": {
            "name": "贵州茅台(示例)",
            "industry": "白酒",
            "price": 1450.0,
            "market_cap": 18200.0,
            "pe_ttm": 26.0,
            "pb": 8.5,
            "roe": 30.0,
            "roe_trend": [27.0, 28.5, 29.0, 30.5, 31.0],
            "eps_history": [35.0, 38.0, 42.0, 47.0, 52.0, 58.0],
            "dividend": [18.0, 20.0, 22.0, 25.0, 28.0],
            "profit_growth": 12.0,
            "revenue_growth": 14.0,
            "dividend_yield": 0.019,
        },
        "000333": {
            "name": "美的集团(示例)",
            "industry": "家用电器",
            "price": 60.0,
            "market_cap": 4200.0,
            "pe_ttm": 13.0,
            "pb": 2.8,
            "roe": 22.0,
            "roe_trend": [21.0, 22.5, 23.0, 22.0, 22.5],
            "eps_history": [3.0, 3.4, 3.8, 4.2, 4.6, 5.0],
            "dividend": [1.5, 1.7, 1.9, 2.1, 2.3],
            "profit_growth": 9.0,
            "revenue_growth": 8.0,
            "dividend_yield": 0.038,
        },
    }

    def __init__(self) -> None:
        self._demo = LocalProvider._DEMO

    def fetch_quote(self, symbol: str, market: str = "A") -> Quote:
        d = self._demo.get(symbol)
        if not d:
            raise FetcherError(f"LocalProvider 无标的 {symbol} 的模拟数据")
        return Quote(
            symbol=symbol,
            name=d["name"],
            market=market,
            price=d["price"],
            market_cap=d["market_cap"],
            pe_ttm=d["pe_ttm"],
            pb=d["pb"],
            dividend_yield=d.get("dividend_yield"),
            as_of=date.today(),
        )

    def fetch_financials(self, symbol: str, market: str = "A") -> Financials:
        d = self._demo.get(symbol)
        if not d:
            raise FetcherError(f"LocalProvider 无标的 {symbol} 的模拟数据")
        return Financials(
            symbol=symbol,
            name=d["name"],
            market=market,
            roe_pct=d["roe"],
            roe_trend=d["roe_trend"],
            revenue_growth_pct=d["revenue_growth"],
            profit_growth_pct=d["profit_growth"],
            industry=d["industry"],
            as_of=date.today(),
        )

    def fetch_bundle(self, symbol: str, market: str = "A") -> StockBundle:
        d = self._demo.get(symbol)
        if not d:
            raise FetcherError(f"LocalProvider 无标的 {symbol} 的模拟数据")
        quote = self.fetch_quote(symbol, market)
        fin = self.fetch_financials(symbol, market)
        fin.eps = d["eps_history"][-1]
        return StockBundle(
            quote=quote,
            financials=fin,
            eps_history=d["eps_history"],
            dividend_history=d["dividend"],
        )


class AkshareProvider(BaseProvider):
    """基于 akshare 的实盘数据源（A股/港股）。

    NOTE: akshare 各接口字段随版本变动，此处为可工作的骨架实现；
    对具体接口若不适用，可在此层做字段映射后再回填到 schema。
    """

    def __init__(self, cache: DiskCache | None = None) -> None:
        self.cache = cache

    def _ak(self):
        try:
            import akshare as ak  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise FetcherError("未安装 akshare，请 pip install akshare") from e
        return ak

    def fetch_quote(self, symbol: str, market: str = "A") -> Quote:
        key = f"quote_{market}_{symbol}"
        if self.cache and (hit := self.cache.get(key)):
            return Quote(**hit)
        ak = self._ak()
        try:
            if market == "HK":
                df = ak.stock_hk_spot_em()
                row = df[df["代码"] == symbol].iloc[0]
                q = Quote(
                    symbol=symbol,
                    name=str(row["名称"]),
                    market=market,
                    price=float(row["最新价"]),
                    change_pct=float(row["涨跌幅"]),
                    market_cap=float(row["总市值"]) / 1e8,
                    as_of=date.today(),
                )
            else:
                df = ak.stock_zh_a_spot_em()
                row = df[df["代码"] == symbol].iloc[0]
                q = Quote(
                    symbol=symbol,
                    name=str(row["名称"]),
                    market=market,
                    price=float(row["最新价"]),
                    change_pct=float(row["涨跌幅"]),
                    pe_ttm=_safe_float(row, "市盈率-动态"),
                    market_cap=float(row["总市值"]) / 1e8,
                    as_of=date.today(),
                )
        except (KeyError, IndexError, ValueError) as e:
            raise FetcherError(f"akshare 拉取 {symbol} 行情失败: {e}") from e
        if self.cache:
            self.cache.set(key, q.model_dump())
        return q

    def fetch_financials(self, symbol: str, market: str = "A") -> Financials:
        # 骨架：实际可对接 ak.stock_financial_abstract / stock_financial_analysis_indicator
        # 这里返回空指标，避免依赖具体接口不稳定。
        return Financials(symbol=symbol, market=market)


def _safe_float(row, col) -> float | None:
    try:
        v = float(row[col])
        return v if v == v else None  # NaN 过滤
    except (KeyError, TypeError, ValueError):
        return None


def resolve_provider(settings: Settings) -> BaseProvider:
    """按配置返回数据提供方，网络不可用时可回退到 LocalProvider。"""
    provider = settings.data.provider
    if provider == "akshare":
        cache = DiskCache(settings.app.data_dir, settings.data.cache_ttl_seconds)
        try:
            p = AkshareProvider(cache)
            # 探测网络/数据源是否可用，不可用则回退
            p.fetch_quote("000001", "A")
            return p
        except (FetcherError, Exception):  # noqa: BLE001
            return LocalProvider()
    if provider == "local":
        return LocalProvider()
    raise FetcherError(f"未知数据源 provider={provider}")

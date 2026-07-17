from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook


USER_AGENT = "sp500-ai-academic-research/0.1 contact=research-project"


def _request_bytes(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Failed to retrieve {url}: {last_error}")


def _request_json(url: str, retries: int = 3) -> Any:
    return json.loads(_request_bytes(url, retries=retries).decode("utf-8"))


def ticker_for_yahoo(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


@dataclass
class PriceResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]
    raw: dict[str, Any]
    provider: str


class StateStreetUniverseProvider:
    """Download a current, dated SPY holdings snapshot from State Street."""

    def __init__(self, url: str):
        self.url = url

    def fetch(self) -> tuple[pd.DataFrame, dict[str, Any], bytes]:
        payload = _request_bytes(self.url)
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
        as_of = str(rows[2][1]) if len(rows) > 2 else ""
        header_index = next(
            i for i, row in enumerate(rows) if row and str(row[0]).strip() == "Name"
        )
        headers = [str(value).strip() if value is not None else "" for value in rows[header_index]]
        records: list[dict[str, Any]] = []
        for row in rows[header_index + 1 :]:
            name = row[0] if row else None
            ticker = row[1] if len(row) > 1 else None
            if not name or not ticker:
                continue
            if str(ticker).upper() in {"USD", "CASH_USD"}:
                continue
            if str(name).upper().startswith("CONTRA "):
                # Temporary corporate-action receivables can appear in fund holdings
                # but are not S&P 500 constituent securities.
                continue
            record = {headers[i]: row[i] for i in range(min(len(headers), len(row))) if headers[i]}
            records.append(
                {
                    "symbol": str(record.get("Ticker", "")).strip(),
                    "company": str(record.get("Name", "")).strip(),
                    "identifier": str(record.get("Identifier", "")).strip(),
                    "sedol": str(record.get("SEDOL", "")).strip(),
                    "weight": pd.to_numeric(record.get("Weight"), errors="coerce"),
                    "sector": "" if record.get("Sector") in (None, "-") else str(record.get("Sector")),
                    "shares_held": pd.to_numeric(record.get("Shares Held"), errors="coerce"),
                    "currency": str(record.get("Local Currency", "")).strip(),
                }
            )
        frame = pd.DataFrame.from_records(records)
        metadata = {
            "provider": "State Street SPY daily holdings",
            "source_url": self.url,
            "as_of_label": as_of,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "security_count": int(len(frame)),
        }
        return frame, metadata, payload


class YahooChartProvider:
    """No-key EOD prototype source. Not the production research provider."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch(self, symbol: str, start: date, end: date) -> PriceResult:
        yahoo_symbol = ticker_for_yahoo(symbol)
        period1 = int(datetime.combine(start, dt_time.min, timezone.utc).timestamp())
        period2 = int(datetime.combine(end, dt_time.max, timezone.utc).timestamp())
        url = self.base_url.format(symbol=urllib.parse.quote(yahoo_symbol))
        params = urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        raw = _request_json(f"{url}?{params}")
        chart = raw.get("chart", {})
        if chart.get("error"):
            raise RuntimeError(f"Yahoo chart error for {symbol}: {chart['error']}")
        result = (chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"No price result for {symbol}")
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        adjclose = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get(
            "adjclose", []
        )
        records = []
        for i, timestamp in enumerate(timestamps):
            def value(key: str) -> Any:
                values = quote.get(key) or []
                return values[i] if i < len(values) else None

            records.append(
                {
                    "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                    "open": value("open"),
                    "high": value("high"),
                    "low": value("low"),
                    "close": value("close"),
                    "adj_close": adjclose[i] if i < len(adjclose) else value("close"),
                    "volume": value("volume"),
                }
            )
        frame = pd.DataFrame.from_records(records)
        if frame.empty:
            raise RuntimeError(f"Empty price history for {symbol}")
        frame["date"] = pd.to_datetime(frame["date"])
        for column in ["open", "high", "low", "close", "adj_close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["adj_close"]).sort_values("date").reset_index(drop=True)
        metadata = dict(result.get("meta") or {})
        metadata.update(
            {
                "requested_symbol": symbol,
                "provider_symbol": yahoo_symbol,
                "source_url": url,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "research_status": "PROTOTYPE_UNOFFICIAL_NO_KEY_SOURCE",
            }
        )
        return PriceResult(frame=frame, metadata=metadata, raw=raw, provider="Yahoo chart")


class FMPProvider:
    """Production adapter for Financial Modeling Prep stable endpoints."""

    def __init__(self, api_key: str, base_url: str):
        if not api_key:
            raise ValueError("FMP_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _get(self, endpoint: str, **params: Any) -> Any:
        params["apikey"] = self.api_key
        query = urllib.parse.urlencode(params)
        return _request_json(f"{self.base_url}/{endpoint}?{query}")

    def fetch_universe(self) -> pd.DataFrame:
        data = self._get("sp500-constituent")
        frame = pd.DataFrame(data)
        frame = frame.rename(columns={"name": "company", "companyName": "company", "subSector": "sub_sector"})
        required = {"symbol", "company", "sector"}
        missing = required - set(frame.columns)
        if missing:
            raise RuntimeError(f"FMP constituent response is missing columns: {sorted(missing)}")
        return frame

    def fetch_prices(self, symbol: str, start: date, end: date) -> PriceResult:
        data = self._get(
            "historical-price-eod/dividend-adjusted",
            symbol=symbol,
            **{"from": start.isoformat(), "to": end.isoformat()},
        )
        records = data.get("historical", data) if isinstance(data, dict) else data
        frame = pd.DataFrame(records)
        if frame.empty:
            raise RuntimeError(f"FMP returned no price history for {symbol}")
        frame = frame.rename(columns={"adjClose": "adj_close"})
        if "adj_close" not in frame:
            frame["adj_close"] = frame["close"]
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").reset_index(drop=True)
        metadata = {
            "requested_symbol": symbol,
            "source_endpoint": "historical-price-eod/dividend-adjusted",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        return PriceResult(frame=frame, metadata=metadata, raw={"data": data}, provider="FMP")

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        metrics = self._get("key-metrics-ttm", symbol=symbol)
        ratios = self._get("ratios-ttm", symbol=symbol)
        first_metrics = metrics[0] if isinstance(metrics, list) and metrics else {}
        first_ratios = ratios[0] if isinstance(ratios, list) and ratios else {}
        return {**first_metrics, **first_ratios}

    def fetch_analyst_consensus(self, symbol: str) -> dict[str, Any]:
        data = self._get("grades-consensus", symbol=symbol)
        return data[0] if isinstance(data, list) and data else (data or {})

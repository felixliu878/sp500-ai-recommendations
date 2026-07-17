from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis import (
    RATING_SCORE,
    analyst_score,
    attach_analyst_comparison,
    condition_flip_summary,
    score_to_rating,
)
from .config import ExperimentConfig, PROJECT_ROOT
from .features import build_price_history_packet, compute_features
from .llm import DryRunClient, OpenAIResponsesClient
from .prompts import PromptBuilder, packet_for_condition
from .providers import FMPProvider, PriceResult, StateStreetUniverseProvider, YahooChartProvider


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _cache_price(result: PriceResult, cache_dir: Path, symbol: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "-")
    result.frame.to_csv(cache_dir / f"{safe}.csv", index=False)
    _write_json(cache_dir / f"{safe}.metadata.json", result.metadata)
    _write_json(cache_dir / f"{safe}.raw.json", result.raw)


def _load_cached_price(cache_dir: Path, symbol: str) -> PriceResult | None:
    safe = symbol.replace("/", "-")
    csv_path = cache_dir / f"{safe}.csv"
    metadata_path = cache_dir / f"{safe}.metadata.json"
    raw_path = cache_dir / f"{safe}.raw.json"
    if not (csv_path.exists() and metadata_path.exists() and raw_path.exists()):
        return None
    frame = pd.read_csv(csv_path, parse_dates=["date"])
    return PriceResult(
        frame=frame,
        metadata=json.loads(metadata_path.read_text(encoding="utf-8")),
        raw=json.loads(raw_path.read_text(encoding="utf-8")),
        provider="cache",
    )


def _json_cell(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _normalize_consensus(symbol: str, raw: dict[str, Any]) -> dict[str, Any]:
    counts = {
        "strong_buy": raw.get("strongBuy", raw.get("strong_buy", 0)) or 0,
        "buy": raw.get("buy", 0) or 0,
        "hold": raw.get("hold", 0) or 0,
        "sell": raw.get("sell", 0) or 0,
        "strong_sell": raw.get("strongSell", raw.get("strong_sell", 0)) or 0,
    }
    score = analyst_score(counts)
    return {
        "symbol": symbol,
        **counts,
        "analyst_count": int(sum(int(v) for v in counts.values())),
        "analyst_score": score,
        "analyst_rating": score_to_rating(score),
        "raw_consensus": _json_cell(raw),
    }


def _save_universe_snapshot(config: ExperimentConfig, output_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    provider = StateStreetUniverseProvider(config.sources["spy_holdings"])
    frame, metadata, payload = provider.fetch()
    snapshot_dir = output_dir / "universe_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "spy_holdings.xlsx").write_bytes(payload)
    frame.to_csv(snapshot_dir / "spy_holdings.csv", index=False)
    _write_json(snapshot_dir / "metadata.json", metadata)
    return frame, metadata


def run_experiment(
    config: ExperimentConfig,
    scope: str,
    backend: str,
    repetitions: int | None = None,
    refresh: bool = False,
    symbols: list[str] | None = None,
    limit: int | None = None,
) -> Path:
    if scope not in {"pilot", "full"}:
        raise ValueError("scope must be pilot or full")
    if backend not in {"dry-run", "openai"}:
        raise ValueError("backend must be dry-run or openai")

    run_label = f"{scope}_{config.as_of_date}_{backend.replace('-', '_')}"
    output_dir = PROJECT_ROOT / "outputs" / run_label
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "prompts").mkdir(exist_ok=True)
    (output_dir / "raw_responses").mkdir(exist_ok=True)
    universe_snapshot, universe_metadata = _save_universe_snapshot(config, output_dir)

    fmp_key = os.getenv("FMP_API_KEY", "")
    fmp = FMPProvider(fmp_key, config.sources["fmp_base"]) if fmp_key else None
    if scope == "pilot":
        universe = pd.read_csv(PROJECT_ROOT / "data" / "pilot_universe.csv")
    elif fmp:
        universe = fmp.fetch_universe()
    else:
        universe = universe_snapshot.copy()
    universe["symbol"] = universe["symbol"].astype(str).str.strip()
    if symbols:
        wanted = {item.upper() for item in symbols}
        universe = universe[universe["symbol"].str.upper().isin(wanted)]
    if limit:
        universe = universe.head(limit)
    if universe.empty:
        raise RuntimeError("The selected universe is empty")
    universe.to_csv(output_dir / "experiment_universe.csv", index=False)

    as_of = date.fromisoformat(config.as_of_date)
    start = as_of - timedelta(days=int(config.price_lookback_days))
    price_provider: Any = fmp or YahooChartProvider(config.sources["yahoo_chart"])
    provider_name = "fmp" if fmp else "yahoo_prototype"
    cache_dir = PROJECT_ROOT / "data" / "raw" / "prices" / provider_name / config.as_of_date

    def fetch_price(symbol: str) -> PriceResult:
        cached = None if refresh else _load_cached_price(cache_dir, symbol)
        if cached:
            return cached
        result = price_provider.fetch_prices(symbol, start, as_of) if fmp else price_provider.fetch(symbol, start, as_of)
        _cache_price(result, cache_dir, symbol)
        return result

    benchmark_symbol = str(config.benchmark_symbol)
    benchmark_frame = fetch_price(benchmark_symbol).frame
    benchmark_features = compute_features(benchmark_frame)
    sector_symbols = {
        str(row.get("sector", "")): config.sector_etfs.get(str(row.get("sector", "")))
        for _, row in universe.iterrows()
    }
    sector_frames: dict[str, pd.DataFrame] = {}
    sector_features: dict[str, dict[str, Any]] = {}
    for sector, etf in sorted(sector_symbols.items()):
        if etf and etf not in sector_frames:
            frame = fetch_price(etf).frame
            sector_frames[etf] = frame
            sector_features[etf] = compute_features(frame, benchmark=benchmark_frame)

    features_by_symbol: dict[str, dict[str, Any]] = {}
    history_by_symbol: dict[str, list[dict[str, Any]]] = {}
    fundamentals_by_symbol: dict[str, dict[str, Any] | None] = {}
    analyst_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for _, company_row in universe.iterrows():
        symbol = str(company_row["symbol"])
        sector = str(company_row.get("sector", ""))
        stock_frame = fetch_price(symbol).frame
        sector_etf = config.sector_etfs.get(sector)
        features = compute_features(
            stock_frame,
            benchmark=benchmark_frame,
            sector_frame=sector_frames.get(sector_etf),
        )
        features_by_symbol[symbol] = features
        history_by_symbol[symbol] = build_price_history_packet(stock_frame)
        fundamentals_by_symbol[symbol] = fmp.fetch_fundamentals(symbol) if fmp else None
        feature_rows.append({"symbol": symbol, "company": company_row.get("company", ""), "sector": sector, **features})
        if fmp:
            analyst_rows.append(_normalize_consensus(symbol, fmp.fetch_analyst_consensus(symbol)))
    pd.DataFrame(feature_rows).to_csv(output_dir / "features.csv", index=False)

    analyst_columns = [
        "symbol", "strong_buy", "buy", "hold", "sell", "strong_sell", "analyst_count",
        "analyst_score", "analyst_rating", "raw_consensus",
    ]
    analyst_frame = pd.DataFrame(analyst_rows, columns=analyst_columns)
    analyst_frame.to_csv(output_dir / "analyst_consensus.csv", index=False)

    if backend == "openai":
        client: Any = OpenAIResponsesClient(
            os.getenv("OPENAI_API_KEY", ""), config.model, str(config.reasoning_effort)
        )
    else:
        client = DryRunClient()
    prompt_builder = PromptBuilder(config.prompt_dir, str(config.prompt_version))
    repetitions = int(repetitions or (config.pilot_repetitions if scope == "pilot" else config.full_repetitions))

    records: list[dict[str, Any]] = []
    jsonl_path = output_dir / "recommendations.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as jsonl:
        for _, company_row in universe.iterrows():
            symbol = str(company_row["symbol"])
            company = str(company_row.get("company", symbol))
            sector = str(company_row.get("sector", ""))
            sector_etf = config.sector_etfs.get(sector)
            for condition in config.conditions:
                packet = packet_for_condition(
                    condition,
                    history_by_symbol[symbol],
                    features_by_symbol[symbol],
                    fundamentals_by_symbol[symbol],
                    benchmark_features,
                    sector_features.get(sector_etf),
                )
                prompt = prompt_builder.build(
                    condition,
                    company,
                    symbol,
                    sector,
                    config.as_of_date,
                    int(config.horizon_months),
                    packet,
                )
                for repetition in range(1, repetitions + 1):
                    stem = f"{symbol}_{condition}_r{repetition}"
                    (output_dir / "prompts" / f"{stem}.txt").write_text(prompt, encoding="utf-8")
                    if backend == "openai":
                        result = client.assess(prompt, allow_web=condition == "D_CURRENT_WEB")
                    else:
                        result = client.assess(
                            symbol,
                            config.as_of_date,
                            int(config.horizon_months),
                            condition,
                            features_by_symbol[symbol],
                        )
                    _write_json(output_dir / "raw_responses" / f"{stem}.json", result.raw)
                    payload = result.assessment
                    record = {
                        "symbol": symbol,
                        "company": company,
                        "sector": sector,
                        "condition": condition,
                        "repetition": repetition,
                        "rating": payload["rating"],
                        "rating_score": RATING_SCORE[payload["rating"]],
                        "confidence_0_100": payload["confidence_0_100"],
                        "reason_codes": _json_cell(payload["reason_codes"]),
                        "thesis": _json_cell(payload["thesis"]),
                        "risks": _json_cell(payload["risks"]),
                        "evidence": _json_cell(payload["evidence"]),
                        "analyst_opinions_encountered": payload["analyst_opinions_encountered"],
                        "analyst_opinions_used": payload["analyst_opinions_used"],
                        "limitations": _json_cell(payload["limitations"]),
                        "backend": result.metadata["backend"],
                        "model": result.metadata.get("model"),
                        "prompt_version": config.prompt_version,
                        "web_search_enabled": result.metadata["web_search_enabled"],
                        "retrieved_source_urls": _json_cell(result.metadata.get("retrieved_source_urls", [])),
                        "input_tokens": result.metadata.get("input_tokens"),
                        "output_tokens": result.metadata.get("output_tokens"),
                        "total_tokens": result.metadata.get("total_tokens"),
                    }
                    records.append(record)
                    jsonl.write(json.dumps({**record, "assessment": payload}, default=str) + "\n")

    recommendations = attach_analyst_comparison(pd.DataFrame(records), analyst_frame)
    recommendations.to_csv(output_dir / "recommendations.csv", index=False)
    condition_flip_summary(recommendations).to_csv(output_dir / "condition_changes.csv", index=False)
    manifest = {
        "experiment_name": config.experiment_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_date": config.as_of_date,
        "scope": scope,
        "backend": backend,
        "backend_disclaimer": (
            "DRY_RUN_NOT_AI: deterministic routing validation only; no AI or web-search recommendation was produced."
            if backend == "dry-run"
            else "OpenAI Responses API"
        ),
        "model": config.model if backend == "openai" else None,
        "symbols": universe["symbol"].tolist(),
        "conditions": list(config.conditions),
        "repetitions": repetitions,
        "planned_or_completed_assessments": len(records),
        "price_provider": "Financial Modeling Prep" if fmp else "Yahoo chart prototype (unofficial)",
        "analyst_provider": "Financial Modeling Prep" if fmp else None,
        "universe_snapshot": universe_metadata,
        "key_status": {"OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")), "FMP_API_KEY": bool(fmp_key)},
    }
    _write_json(output_dir / "manifest.json", manifest)
    return output_dir

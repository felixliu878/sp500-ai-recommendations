from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import time
import tempfile
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
from .llm import DryRunClient, OpenAIResponsesClient, validate_assessment
from .prompts import PromptBuilder, packet_for_condition
from .providers import (
    PriceResult,
    ResearchResult,
    StateStreetUniverseProvider,
    YAHOO_FUNDAMENTAL_METRICS,
    YAHOO_FUNDAMENTAL_OUTPUT_NAMES,
    YAHOO_METRIC_SET_SHA256,
    YAHOO_RESEARCH_SCHEMA_VERSION,
    YahooChartProvider,
    YahooResearchProvider,
)

PRICE_CACHE_SCHEMA_VERSION = "price-cache-v2"
YAHOO_FUNDAMENTAL_METADATA_FIELDS = {
    "locally_derived",
    "missing_metrics",
    "data_retrieved_at_utc",
    "data_cutoff",
    "source",
}
YAHOO_LOCALLY_DERIVED_FIELDS = {
    "operating_margin_ttm",
    "net_margin_ttm",
    "free_cash_flow_margin_ttm",
    "debt_to_equity_latest_quarter",
    "net_cash_latest_quarter",
}


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    _atomic_write_bytes(path, frame.to_csv(index=False).encode("utf-8"))


def _cache_price(
    result: PriceResult,
    cache_dir: Path,
    symbol: str,
    start: date | None = None,
    end: date | None = None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "-")
    _write_frame(cache_dir / f"{safe}.csv", result.frame)
    metadata = dict(result.metadata)
    if start is not None and end is not None:
        metadata.update(
            {
                "cache_schema_version": PRICE_CACHE_SCHEMA_VERSION,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
            }
        )
    _write_json(cache_dir / f"{safe}.metadata.json", metadata)
    _write_json(cache_dir / f"{safe}.raw.json", result.raw)


def _load_cached_price(
    cache_dir: Path, symbol: str, start: date, end: date
) -> PriceResult | None:
    safe = symbol.replace("/", "-")
    csv_path = cache_dir / f"{safe}.csv"
    metadata_path = cache_dir / f"{safe}.metadata.json"
    raw_path = cache_dir / f"{safe}.raw.json"
    if not (csv_path.exists() and metadata_path.exists() and raw_path.exists()):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(csv_path, parse_dates=["date"]).sort_values("date")
    except (OSError, ValueError, json.JSONDecodeError, pd.errors.ParserError):
        return None
    if metadata.get("cache_schema_version") != PRICE_CACHE_SCHEMA_VERSION:
        return None
    if metadata.get("requested_start") != start.isoformat():
        return None
    if metadata.get("requested_end") != end.isoformat():
        return None
    if frame.empty or frame["date"].isna().any():
        return None
    if frame["date"].max().date() > end:
        return None
    frame = frame[
        (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)
    ].reset_index(drop=True)
    if frame.empty:
        return None
    return PriceResult(
        frame=frame,
        metadata=metadata,
        raw=raw,
        provider="cache",
    )


def _cache_research(result: ResearchResult, cache_dir: Path, symbol: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "-")
    _write_json(
        cache_dir / f"{safe}.json",
        {
            "fundamentals": result.fundamentals,
            "analyst_consensus": result.analyst_consensus,
            "metadata": result.metadata,
            "raw": result.raw,
            "provider": result.provider,
        },
    )


def _load_cached_research(
    cache_dir: Path, symbol: str, cutoff: date
) -> ResearchResult | None:
    safe = symbol.replace("/", "-")
    path = cache_dir / f"{safe}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata") or {}
    fundamentals = payload.get("fundamentals") or {}
    analyst_consensus = payload.get("analyst_consensus") or {}
    if not all(
        isinstance(value, dict)
        for value in (metadata, fundamentals, analyst_consensus)
    ):
        return None
    if metadata.get("schema_version") != YAHOO_RESEARCH_SCHEMA_VERSION:
        return None
    if metadata.get("metric_set_sha256") != YAHOO_METRIC_SET_SHA256:
        return None
    if metadata.get("data_cutoff") != cutoff.isoformat():
        return None
    if fundamentals.get("data_cutoff") != cutoff.isoformat():
        return None
    if not _timestamp_matches_cutoff(metadata.get("retrieved_at_utc"), cutoff):
        return None
    if not _timestamp_matches_cutoff(
        fundamentals.get("data_retrieved_at_utc"), cutoff
    ):
        return None
    if not _timestamp_matches_cutoff(
        analyst_consensus.get("retrievedAtUtc"), cutoff
    ):
        return None
    if metadata.get("errors") or fundamentals.get("status") == "unavailable":
        return None
    allowed_fundamental_fields = (
        set(YAHOO_FUNDAMENTAL_OUTPUT_NAMES.values())
        | YAHOO_FUNDAMENTAL_METADATA_FIELDS
    )
    if set(fundamentals) - allowed_fundamental_fields:
        return None
    locally_derived = fundamentals.get("locally_derived")
    if locally_derived is not None and (
        not isinstance(locally_derived, dict)
        or set(locally_derived) - YAHOO_LOCALLY_DERIVED_FIELDS
    ):
        return None
    missing_metrics = fundamentals.get("missing_metrics")
    if (
        not isinstance(missing_metrics, list)
        or len(missing_metrics) > 4
        or len(set(missing_metrics)) != len(missing_metrics)
        or any(metric not in YAHOO_FUNDAMENTAL_METRICS for metric in missing_metrics)
    ):
        return None
    for field in YAHOO_FUNDAMENTAL_OUTPUT_NAMES.values():
        if field not in fundamentals:
            continue
        packet = fundamentals[field]
        if not isinstance(packet, dict) or "value" not in packet:
            return None
        raw_date = packet.get("as_of_date")
        if not isinstance(raw_date, str):
            return None
        try:
            observed = date.fromisoformat(raw_date)
        except ValueError:
            return None
        if observed.isoformat() != raw_date or observed > cutoff:
            return None
    if not any(
        field in fundamentals for field in YAHOO_FUNDAMENTAL_OUTPUT_NAMES.values()
    ):
        return None
    return ResearchResult(
        fundamentals=fundamentals,
        analyst_consensus=analyst_consensus,
        metadata=metadata,
        raw=payload.get("raw") or {},
        provider=str(payload.get("provider") or "cache"),
    )


def _json_cell(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_error(exc: Exception) -> str:
    text = re.sub(
        r"sk-[A-Za-z0-9_-]+",
        "[REDACTED]",
        f"{type(exc).__name__}: {exc}",
    )
    text = re.sub(
        r"(?i)([?&](?:api[_-]?key|apikey|access[_-]?token|token|key)=)[^&\s]+",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(
        r"(?i)(\b(?:authorization|bearer)\b\s*[:=]?\s*)[A-Za-z0-9._-]{8,}",
        r"\1[REDACTED]",
        text,
    )


def _timestamp_matches_cutoff(value: Any, cutoff: date) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.astimezone(timezone.utc).date() == cutoff


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return int(value)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return int(value)


def _nonnegative_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite nonnegative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return result


def _bounded_price_result(
    result: PriceResult, symbol: str, start: date, end: date
) -> PriceResult:
    frame = result.frame.copy()
    if "date" not in frame or "adj_close" not in frame:
        raise RuntimeError(f"Price result for {symbol} is missing required columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].isna().any():
        raise RuntimeError(f"Price result for {symbol} contains invalid dates")
    if frame["date"].max().date() > end:
        raise RuntimeError(f"Price result for {symbol} extends past the cutoff")
    frame = frame[
        (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)
    ]
    frame = (
        frame.dropna(subset=["adj_close"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise RuntimeError(f"Price result for {symbol} is empty inside the requested window")
    return PriceResult(
        frame=frame,
        metadata=result.metadata,
        raw=result.raw,
        provider=result.provider,
    )


def _price_quality_row(label: str, frame: pd.DataFrame, cutoff: date) -> dict[str, Any]:
    price_start = pd.Timestamp(frame["date"].min()).date()
    price_end = pd.Timestamp(frame["date"].max()).date()
    return {
        "context": label,
        "price_observations": int(len(frame)),
        "price_data_start": price_start.isoformat(),
        "price_data_end": price_end.isoformat(),
        "price_lag_days": (cutoff - price_end).days,
    }


ANALYST_EVIDENCE_TERMS = (
    "analyst",
    "analyst rating",
    "analyst ratings",
    "price target",
    "consensus",
    "upgrade",
    "downgrade",
    "overweight",
    "underweight",
)


def _analyst_exposure_audit(payload: dict[str, Any], condition: str) -> str:
    if condition != "D_CURRENT_WEB":
        return "NOT_APPLICABLE_NO_WEB"
    evidence_text = json.dumps(
        {
            "evidence": payload.get("evidence", []),
            "thesis": payload.get("thesis", []),
            "risks": payload.get("risks", []),
        },
        sort_keys=True,
    ).lower()
    if any(
        re.search(rf"\b{re.escape(term)}\b", evidence_text)
        for term in ANALYST_EVIDENCE_TERMS
    ):
        return "DETECTED_IN_MODEL_OUTPUT_EVIDENCE"
    return "NOT_DETECTED_IN_MODEL_OUTPUT_EVIDENCE"


def _portal_rating(raw_key: Any) -> str | None:
    normalized = str(raw_key or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "strong_buy": "STRONG_BUY",
        "buy": "BUY",
        "hold": "HOLD",
        "neutral": "HOLD",
        "underperform": "SELL",
        "sell": "SELL",
        "strong_sell": "STRONG_SELL",
    }.get(normalized)


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
        "count_normalized_score": score,
        "count_normalized_rating": score_to_rating(score),
        "portal_rating_yahoo": _portal_rating(raw.get("portalRecommendationKey")),
        "portal_recommendation_key_yahoo": raw.get("portalRecommendationKey"),
        "portal_recommendation_mean_yahoo": raw.get("portalRecommendationMean"),
        "portal_analyst_count_yahoo": raw.get("portalNumberOfAnalystOpinions"),
        "consensus_period": raw.get("period"),
        "price_target_current": raw.get("priceTargetCurrent"),
        "price_target_low": raw.get("priceTargetLow"),
        "price_target_mean": raw.get("priceTargetMean"),
        "price_target_median": raw.get("priceTargetMedian"),
        "price_target_high": raw.get("priceTargetHigh"),
        "retrieved_at_utc": raw.get("retrievedAtUtc"),
        "raw_consensus": _json_cell(raw),
    }


def _analyst_benchmark_coverage(
    quality_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    distribution_available = 0
    portal_available = 0
    for row in quality_rows:
        symbol = str(row["symbol"])
        if int(row["analyst_count_current_distribution"]) > 0:
            distribution_available += 1
        else:
            issues.append(
                {"symbol": symbol, "issue": "current analyst distribution is missing"}
            )
        if bool(row["portal_rating_available"]):
            portal_available += 1
        else:
            issues.append(
                {"symbol": symbol, "issue": "Yahoo portal rating is missing"}
            )
    return {
        "status": "COMPLETE" if not issues else "PARTIAL",
        "issues": issues,
        "analyst_distribution_available": distribution_available,
        "portal_rating_available": portal_available,
        "security_count": len(quality_rows),
    }


def _save_universe_snapshot(config: ExperimentConfig, output_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    provider = StateStreetUniverseProvider(config.sources["spy_holdings"])
    frame, metadata, payload = provider.fetch()
    snapshot_dir = output_dir / "universe_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(snapshot_dir / "spy_holdings.xlsx", payload)
    _write_frame(snapshot_dir / "spy_holdings.csv", frame)
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
    resolved_limit = (
        _positive_int(limit, "limit") if limit is not None else None
    )
    resolved_repetitions = _positive_int(
        repetitions
        if repetitions is not None
        else (
            config.pilot_repetitions
            if scope == "pilot"
            else config.full_repetitions
        ),
        "repetitions",
    )
    horizon_months = _positive_int(config.horizon_months, "horizon_months")
    price_lookback_days = _positive_int(
        config.price_lookback_days, "price_lookback_days"
    )
    price_history_frequency = str(
        config.raw.get("price_history_frequency", "monthly")
    ).strip().lower()
    if price_history_frequency != "monthly":
        raise ValueError("price_history_frequency must be 'monthly'")
    price_history_observations = _positive_int(
        config.raw.get("price_history_observations", 24),
        "price_history_observations",
    )
    max_output_tokens = _positive_int(
        config.raw.get("max_output_tokens", 3000), "max_output_tokens"
    )
    web_max_tool_calls = _positive_int(
        config.raw.get("web_max_tool_calls", 4), "web_max_tool_calls"
    )
    consecutive_failure_limit = _positive_int(
        config.raw.get("consecutive_failure_limit", 3),
        "consecutive_failure_limit",
    )
    total_failure_limit = _positive_int(
        config.raw.get("total_failure_limit", 6), "total_failure_limit"
    )
    request_delay = _nonnegative_float(
        config.raw.get("yahoo_request_delay_seconds", 0.0),
        "yahoo_request_delay_seconds",
    )
    execution_seed = _integer(
        config.raw.get("execution_seed", 20260724), "execution_seed"
    )
    try:
        as_of = date.fromisoformat(config.as_of_date)
    except ValueError as exc:
        raise ValueError("as_of_date must use YYYY-MM-DD format") from exc
    if as_of.isoformat() != config.as_of_date:
        raise ValueError("as_of_date must use YYYY-MM-DD format")
    provider_mode = str(config.raw.get("data_provider", "yahoo")).strip().lower()
    if provider_mode != "yahoo":
        raise ValueError(
            "Only data_provider='yahoo' is currently enabled. FMP is disabled "
            "because its configured fundamentals and analyst endpoints are not "
            "cutoff-safe for this protocol."
        )

    run_started_at = datetime.now(timezone.utc)
    run_id = run_started_at.strftime("%Y%m%dT%H%M%S%fZ")
    run_label = f"{scope}_{config.as_of_date}_{backend.replace('-', '_')}_{run_id}"
    output_dir = PROJECT_ROOT / "outputs" / run_label
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "prompts").mkdir(exist_ok=True)
    (output_dir / "raw_responses").mkdir(exist_ok=True)
    universe_snapshot, universe_metadata = _save_universe_snapshot(config, output_dir)

    yahoo_research = YahooResearchProvider()
    if scope == "pilot":
        universe = pd.read_csv(PROJECT_ROOT / "data" / "pilot_universe.csv")
    else:
        universe = universe_snapshot.copy()
    universe["symbol"] = universe["symbol"].astype(str).str.strip()
    if symbols:
        wanted = {item.upper() for item in symbols}
        universe = universe[universe["symbol"].str.upper().isin(wanted)]
    if resolved_limit is not None:
        universe = universe.head(resolved_limit)
    if universe.empty:
        raise RuntimeError("The selected universe is empty")
    universe = universe.reset_index(drop=True)

    start = as_of - timedelta(days=price_lookback_days)
    price_provider = YahooChartProvider(config.sources["yahoo_chart"])
    cache_dir = (
        PROJECT_ROOT
        / "data"
        / "raw"
        / "prices"
        / "yahoo_prototype"
        / config.as_of_date
    )

    def fetch_price(symbol: str) -> PriceResult:
        cached = None if refresh else _load_cached_price(cache_dir, symbol, start, as_of)
        if cached:
            return _bounded_price_result(cached, symbol, start, as_of)
        result = _bounded_price_result(
            price_provider.fetch(symbol, start, as_of),
            symbol,
            start,
            as_of,
        )
        _cache_price(result, cache_dir, symbol, start, as_of)
        return result

    research_cache_dir = (
        PROJECT_ROOT
        / "data"
        / "raw"
        / "research"
        / "yahoo_unofficial"
        / config.as_of_date
    )
    research_by_symbol: dict[str, ResearchResult] = {}
    sector_conflicts: list[dict[str, str]] = []
    for index, company_row in universe.iterrows():
        symbol = str(company_row["symbol"])
        research_result = (
            None
            if refresh
            else _load_cached_research(research_cache_dir, symbol, as_of)
        )
        if research_result is None:
            try:
                research_result = yahoo_research.fetch(symbol, as_of)
            except Exception as exc:
                retrieved_at = datetime.now(timezone.utc).isoformat()
                safe_error = _safe_error(exc)
                research_result = ResearchResult(
                    fundamentals={
                        "status": "unavailable",
                        "error": safe_error,
                        "missing_metrics": sorted(YAHOO_FUNDAMENTAL_METRICS),
                        "data_retrieved_at_utc": retrieved_at,
                        "data_cutoff": config.as_of_date,
                    },
                    analyst_consensus={"retrievedAtUtc": retrieved_at},
                    metadata={
                        "provider": "Yahoo Finance via yfinance",
                        "schema_version": YAHOO_RESEARCH_SCHEMA_VERSION,
                        "metric_set_sha256": YAHOO_METRIC_SET_SHA256,
                        "retrieved_at_utc": retrieved_at,
                        "data_cutoff": config.as_of_date,
                        "sector_yahoo": None,
                        "sector_gics": None,
                        "errors": {"provider": safe_error},
                    },
                    raw={"errors": {"provider": safe_error}},
                    provider="Yahoo Finance via yfinance",
                )
            _cache_research(research_result, research_cache_dir, symbol)
            if request_delay > 0:
                time.sleep(request_delay)
        research_by_symbol[symbol] = research_result

        raw_existing_sector = company_row.get("sector", "")
        existing_sector = (
            ""
            if pd.isna(raw_existing_sector)
            else str(raw_existing_sector).strip()
        )
        yahoo_sector = str(
            research_result.metadata.get("sector_gics") or ""
        ).strip()
        configured_override = (config.raw.get("sector_overrides") or {}).get(symbol) or {}
        override_sector = str(configured_override.get("sector") or "").strip()
        override_source = str(configured_override.get("source") or "").strip()
        if (
            existing_sector
            and existing_sector != "-"
            and yahoo_sector
            and existing_sector != yahoo_sector
        ):
            sector_conflicts.append(
                {
                    "symbol": symbol,
                    "existing_sector": existing_sector,
                    "yahoo_sector": yahoo_sector,
                }
            )
        resolved_sector = yahoo_sector or override_sector or (
            existing_sector if existing_sector != "-" else ""
        )
        universe.at[index, "sector"] = resolved_sector
        universe.at[index, "sector_source"] = (
            "YAHOO_INFO"
            if yahoo_sector
            else (
                "VERIFIED_CONFIG_OVERRIDE"
                if override_sector
                else ("SUPPLIED_UNIVERSE_FALLBACK" if resolved_sector else "UNRESOLVED")
            )
        )
        universe.at[index, "sector_source_reference"] = (
            "Yahoo Finance metadata"
            if yahoo_sector
            else (override_source if override_sector else "")
        )

    _write_frame(output_dir / "experiment_universe.csv", universe)

    benchmark_symbol = str(config.benchmark_symbol)
    benchmark_frame = fetch_price(benchmark_symbol).frame
    benchmark_features = compute_features(benchmark_frame)
    context_quality_rows = [
        _price_quality_row(
            f"benchmark:{benchmark_symbol}", benchmark_frame, as_of
        )
    ]
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
            context_quality_rows.append(
                _price_quality_row(f"sector:{sector}:{etf}", frame, as_of)
            )
    _write_frame(
        output_dir / "context_data_quality.csv", pd.DataFrame(context_quality_rows)
    )

    features_by_symbol: dict[str, dict[str, Any]] = {}
    history_by_symbol: dict[str, list[dict[str, Any]]] = {}
    fundamentals_by_symbol: dict[str, dict[str, Any] | None] = {}
    analyst_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    for _, company_row in universe.iterrows():
        symbol = str(company_row["symbol"])
        sector = str(company_row.get("sector", ""))
        sector_source = str(company_row.get("sector_source", ""))
        stock_frame = fetch_price(symbol).frame
        sector_etf = config.sector_etfs.get(sector)
        features = compute_features(
            stock_frame,
            benchmark=benchmark_frame,
            sector_frame=sector_frames.get(sector_etf),
        )
        features_by_symbol[symbol] = features
        history_by_symbol[symbol] = build_price_history_packet(
            stock_frame, months=price_history_observations
        )
        research_result = research_by_symbol[symbol]
        fundamentals_by_symbol[symbol] = research_result.fundamentals
        analyst_raw = research_result.analyst_consensus
        research_metadata = research_result.metadata
        feature_rows.append(
            {
                "symbol": symbol,
                "company": company_row.get("company", ""),
                "sector": sector,
                "sector_source": sector_source,
                **features,
            }
        )
        normalized_consensus = _normalize_consensus(symbol, analyst_raw)
        analyst_rows.append(normalized_consensus)
        price_end = pd.Timestamp(stock_frame["date"].max()).date()
        quality_rows.append(
            {
                "symbol": symbol,
                "price_observations": int(len(stock_frame)),
                "price_data_start": pd.Timestamp(stock_frame["date"].min()).date().isoformat(),
                "price_data_end": price_end.isoformat(),
                "price_lag_days": (as_of - price_end).days,
                "fundamental_fields": int(
                    len(
                        [
                            key
                            for key in YAHOO_FUNDAMENTAL_OUTPUT_NAMES.values()
                            if key in (fundamentals_by_symbol[symbol] or {})
                        ]
                    )
                ),
                "missing_fundamental_metrics": _json_cell(
                    (fundamentals_by_symbol[symbol] or {}).get("missing_metrics", [])
                ),
                "research_errors": _json_cell(research_metadata.get("errors") or {}),
                "research_data_cutoff": research_metadata.get("data_cutoff"),
                "sector": sector,
                "sector_source": sector_source,
                "sector_etf": sector_etf,
                "sector_resolved": bool(sector),
                "sector_etf_available": bool(sector_etf),
                "analyst_count_current_distribution": normalized_consensus["analyst_count"],
                "portal_rating_available": bool(
                    normalized_consensus.get("portal_rating_yahoo")
                ),
            }
        )
    _write_frame(output_dir / "features.csv", pd.DataFrame(feature_rows))

    analyst_columns = [
        "symbol", "strong_buy", "buy", "hold", "sell", "strong_sell", "analyst_count",
        "count_normalized_score", "count_normalized_rating", "portal_rating_yahoo",
        "portal_recommendation_key_yahoo", "portal_recommendation_mean_yahoo",
        "portal_analyst_count_yahoo", "consensus_period", "price_target_current",
        "price_target_low", "price_target_mean", "price_target_median",
        "price_target_high", "retrieved_at_utc", "raw_consensus",
    ]
    analyst_frame = pd.DataFrame(analyst_rows, columns=analyst_columns)
    _write_frame(output_dir / "analyst_consensus.csv", analyst_frame)
    quality_frame = pd.DataFrame(quality_rows)
    _write_frame(output_dir / "data_quality.csv", quality_frame)

    model_gate_issues: list[dict[str, Any]] = []
    for row in context_quality_rows:
        context = str(row["context"])
        if int(row["price_observations"]) < 253:
            model_gate_issues.append(
                {"context": context, "issue": "fewer than 253 price observations"}
            )
        if int(row["price_lag_days"]) < 0:
            model_gate_issues.append(
                {"context": context, "issue": "price data extend past cutoff"}
            )
        if int(row["price_lag_days"]) > 7:
            model_gate_issues.append(
                {
                    "context": context,
                    "issue": "price data are stale by more than 7 days",
                }
            )
    for conflict in sector_conflicts:
        model_gate_issues.append(
            {
                "symbol": conflict["symbol"],
                "issue": (
                    "sector conflict between supplied universe "
                    f"({conflict['existing_sector']}) and Yahoo "
                    f"({conflict['yahoo_sector']})"
                ),
            }
        )
    for row in quality_rows:
        symbol = row["symbol"]
        if int(row["price_observations"]) < 253:
            model_gate_issues.append({"symbol": symbol, "issue": "fewer than 253 price observations"})
        if int(row["price_lag_days"]) < 0:
            model_gate_issues.append({"symbol": symbol, "issue": "price data extend past cutoff"})
        if int(row["price_lag_days"]) > 7:
            model_gate_issues.append({"symbol": symbol, "issue": "price data are stale by more than 7 days"})
        if not bool(row["sector_resolved"]):
            model_gate_issues.append({"symbol": symbol, "issue": "GICS sector is unresolved"})
        if not bool(row["sector_etf_available"]):
            model_gate_issues.append(
                {"symbol": symbol, "issue": "sector ETF mapping is missing"}
            )
        if scope == "full" and row["sector_source"] not in {
            "YAHOO_INFO",
            "VERIFIED_CONFIG_OVERRIDE",
        }:
            model_gate_issues.append(
                {
                    "symbol": symbol,
                    "issue": (
                        "full-universe sector was not resolved from Yahoo metadata "
                        "or a documented override"
                    ),
                }
            )
        missing_metrics = json.loads(str(row["missing_fundamental_metrics"]))
        if len(missing_metrics) > 4 or int(row["fundamental_fields"]) < 8:
            model_gate_issues.append(
                {
                    "symbol": symbol,
                    "issue": (
                        "fundamental coverage is below the protocol threshold "
                        f"({len(missing_metrics)} missing; {row['fundamental_fields']} packet fields)"
                    ),
                }
            )
        if json.loads(str(row["research_errors"])):
            model_gate_issues.append({"symbol": symbol, "issue": "research provider returned errors"})
        if row["research_data_cutoff"] != config.as_of_date:
            model_gate_issues.append(
                {"symbol": symbol, "issue": "research data cutoff does not match the run"}
            )
    benchmark_coverage = _analyst_benchmark_coverage(quality_rows)

    acquisition_gate = {
        "status": "PASS" if not model_gate_issues else "FAIL",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "strict_for_this_run": bool(backend == "openai"),
        "issues": model_gate_issues,
        "analyst_benchmark_status": benchmark_coverage["status"],
        "analyst_benchmark_issues": benchmark_coverage["issues"],
        "analyst_distribution_available": benchmark_coverage[
            "analyst_distribution_available"
        ],
        "portal_rating_available": benchmark_coverage["portal_rating_available"],
        "security_count": benchmark_coverage["security_count"],
    }
    _write_json(output_dir / "acquisition_gate.json", acquisition_gate)
    if backend == "openai" and model_gate_issues:
        raise RuntimeError(
            "Acquisition quality gate failed before model calls: "
            f"{model_gate_issues}"
        )

    if backend == "openai":
        client: Any = OpenAIResponsesClient(
            os.getenv("OPENAI_API_KEY", ""),
            config.model,
            str(config.reasoning_effort),
            max_output_tokens,
            web_max_tool_calls,
        )
    else:
        client = DryRunClient()
    prompt_builder = PromptBuilder(config.prompt_dir, str(config.prompt_version))

    tasks: list[dict[str, Any]] = []
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
                horizon_months,
                packet,
            )
            for repetition in range(1, resolved_repetitions + 1):
                tasks.append(
                    {
                        "symbol": symbol,
                        "company": company,
                        "sector": sector,
                        "condition": condition,
                        "repetition": repetition,
                        "packet": packet,
                        "prompt": prompt,
                    }
                )
    random.Random(execution_seed).shuffle(tasks)

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    consecutive_failures = 0
    aborted_after_failure_threshold = False
    jsonl_path = output_dir / "recommendations.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as jsonl:
        for execution_order, task in enumerate(tasks, start=1):
            symbol = task["symbol"]
            company = task["company"]
            sector = task["sector"]
            condition = task["condition"]
            repetition = int(task["repetition"])
            packet = task["packet"]
            prompt = task["prompt"]
            stem = f"{symbol}_{condition}_r{repetition}"
            (output_dir / "prompts" / f"{stem}.txt").write_text(prompt, encoding="utf-8")
            result = None
            try:
                if backend == "openai":
                    result = client.assess(
                        prompt,
                        allow_web=condition == "D_CURRENT_WEB",
                        condition=condition,
                    )
                else:
                    result = client.assess(
                        symbol,
                        config.as_of_date,
                        horizon_months,
                        condition,
                        features_by_symbol[symbol],
                    )
                payload = result.assessment
                expected = {
                    "symbol": symbol,
                    "as_of": config.as_of_date,
                    "horizon_months": horizon_months,
                }
                mismatches = {
                    key: {"expected": value, "received": payload.get(key)}
                    for key, value in expected.items()
                    if payload.get(key) != value
                }
                if mismatches:
                    raise ValueError(f"Assessment identity fields do not match the prompt: {mismatches}")
                validate_assessment(
                    payload,
                    cutoff=config.as_of_date,
                    allow_web=condition == "D_CURRENT_WEB",
                    condition=condition,
                )
                _write_json(output_dir / "raw_responses" / f"{stem}.json", result.raw)
                assessment_created_at = datetime.now(timezone.utc).isoformat()
                record = {
                    "run_id": run_id,
                    "execution_order": execution_order,
                    "assessment_created_at_utc": assessment_created_at,
                    "symbol": symbol,
                    "company": company,
                    "sector": sector,
                    "condition": condition,
                    "repetition": repetition,
                    "rating": payload["rating"],
                    "rating_score": RATING_SCORE[payload["rating"]],
                    "confidence_0_100": payload["confidence_0_100"],
                    "evidence_sufficiency": payload["evidence_sufficiency"],
                    "forced_rating_due_to_insufficient_data": payload[
                        "forced_rating_due_to_insufficient_data"
                    ],
                    "reason_codes": _json_cell(payload["reason_codes"]),
                    "thesis": _json_cell(payload["thesis"]),
                    "risks": _json_cell(payload["risks"]),
                    "evidence": _json_cell(payload["evidence"]),
                    "analyst_opinions_encountered": payload["analyst_opinions_encountered"],
                    "analyst_opinions_used": payload["analyst_opinions_used"],
                    "analyst_terms_in_final_output": _analyst_exposure_audit(
                        payload, condition
                    ),
                    "analyst_terms_detected_in_web_actions": result.metadata.get(
                        "analyst_terms_detected_in_web_actions"
                    ),
                    "analyst_web_action_matched_terms": _json_cell(
                        result.metadata.get("analyst_web_action_matched_terms", [])
                    ),
                    "web_search_call_count": result.metadata.get("web_search_call_count", 0),
                    "web_search_source_count": result.metadata.get(
                        "web_search_source_count", 0
                    ),
                    "limitations": _json_cell(payload["limitations"]),
                    "backend": result.metadata["backend"],
                    "model": result.metadata.get("model"),
                    "requested_model": result.metadata.get("requested_model"),
                    "response_id": result.metadata.get("response_id"),
                    "prompt_version": config.prompt_version,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "packet_sha256": _sha256(packet),
                    "web_search_enabled": result.metadata["web_search_enabled"],
                    "retrieved_source_urls": _json_cell(
                        result.metadata.get("retrieved_source_urls", [])
                    ),
                    "input_tokens": result.metadata.get("input_tokens"),
                    "output_tokens": result.metadata.get("output_tokens"),
                    "total_tokens": result.metadata.get("total_tokens"),
                }
                records.append(record)
                consecutive_failures = 0
                jsonl.write(json.dumps({**record, "assessment": payload}, default=str) + "\n")
                jsonl.flush()
            except Exception as exc:
                failure_raw = getattr(exc, "raw", None)
                failure_metadata = getattr(exc, "metadata", None)
                if failure_raw is None and result is not None:
                    failure_raw = result.raw
                if not isinstance(failure_metadata, dict) and result is not None:
                    failure_metadata = result.metadata
                if not isinstance(failure_metadata, dict):
                    failure_metadata = {}
                failed_raw_artifact = None
                if isinstance(failure_raw, dict):
                    failed_raw_artifact = (
                        f"raw_responses/{stem}.failed_response.json"
                    )
                    _write_json(output_dir / failed_raw_artifact, failure_raw)
                error = {
                    "run_id": run_id,
                    "execution_order": execution_order,
                    "symbol": symbol,
                    "condition": condition,
                    "repetition": repetition,
                    "error": _safe_error(exc),
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "response_id": failure_metadata.get("response_id"),
                    "model": failure_metadata.get("model"),
                    "input_tokens": failure_metadata.get("input_tokens"),
                    "output_tokens": failure_metadata.get("output_tokens"),
                    "total_tokens": failure_metadata.get("total_tokens"),
                    "web_search_call_count": failure_metadata.get(
                        "web_search_call_count"
                    ),
                    "failed_raw_artifact": failed_raw_artifact,
                }
                failures.append(error)
                consecutive_failures += 1
                _write_json(output_dir / "raw_responses" / f"{stem}.error.json", error)
                if backend == "openai" and (
                    consecutive_failures >= consecutive_failure_limit
                    or len(failures) >= total_failure_limit
                ):
                    aborted_after_failure_threshold = True
                    break

    recommendations = attach_analyst_comparison(pd.DataFrame(records), analyst_frame)
    _write_frame(output_dir / "recommendations.csv", recommendations)
    _write_frame(
        output_dir / "condition_changes.csv",
        condition_flip_summary(recommendations),
    )
    failure_columns = [
        "run_id",
        "execution_order",
        "symbol",
        "condition",
        "repetition",
        "error",
        "failed_at_utc",
        "response_id",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "web_search_call_count",
        "failed_raw_artifact",
    ]
    _write_frame(
        output_dir / "assessment_errors.csv",
        pd.DataFrame(failures, columns=failure_columns),
    )
    total_tokens = (
        int(pd.to_numeric(recommendations.get("total_tokens"), errors="coerce").fillna(0).sum())
        if not recommendations.empty
        else 0
    )
    failed_response_total_tokens = int(
        pd.to_numeric(
            pd.Series([row.get("total_tokens") for row in failures]),
            errors="coerce",
        )
        .fillna(0)
        .sum()
    )
    manifest = {
        "experiment_name": config.experiment_name,
        "run_id": run_id,
        "run_started_at_utc": run_started_at.isoformat(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_date": config.as_of_date,
        "prompt_version": config.prompt_version,
        "horizon_months": horizon_months,
        "price_lookback_days": price_lookback_days,
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
        "repetitions": resolved_repetitions,
        "execution_seed": execution_seed,
        "planned_assessments": len(tasks),
        "completed_assessments": len(records),
        "failed_assessments": len(failures),
        "not_attempted_assessments": len(tasks) - len(records) - len(failures),
        "aborted_after_failure_threshold": aborted_after_failure_threshold,
        "consecutive_failure_limit": consecutive_failure_limit,
        "total_failure_limit": total_failure_limit,
        "total_tokens": total_tokens,
        "failed_response_total_tokens": failed_response_total_tokens,
        "recorded_total_tokens_including_failures": (
            total_tokens + failed_response_total_tokens
        ),
        "data_provider_mode": provider_mode,
        "price_provider": "Yahoo chart (unofficial)",
        "price_history_frequency": price_history_frequency,
        "price_history_observations": price_history_observations,
        "web_max_tool_calls": web_max_tool_calls,
        "fundamental_provider": "Yahoo Finance via yfinance (unofficial)",
        "analyst_provider": "Yahoo Finance via yfinance (unofficial)",
        "universe_snapshot": universe_metadata,
        "key_status": {"OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY"))},
        "interpretation_limits": [
            "Yahoo data are current unofficial snapshots, not licensed point-in-time research data.",
            "Analyst labels are an external benchmark and do not share the AI prompt's exact SPY-relative target.",
            "A rating difference without reported analyst use is behavior consistent with independent judgment, not proof of internal reasoning.",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return output_dir

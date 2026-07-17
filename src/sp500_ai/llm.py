from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .analysis import RATING_SCORE, score_to_rating


RATINGS = list(RATING_SCORE)
REASON_CODES = [
    "MOMENTUM",
    "RELATIVE_STRENGTH",
    "TREND",
    "VOLATILITY",
    "VALUATION",
    "FUNDAMENTALS",
    "CATALYST",
    "MACRO",
    "EARNINGS",
    "UNCERTAINTY",
    "INSUFFICIENT_DATA",
]


ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "symbol": {"type": "string"},
        "as_of": {"type": "string"},
        "horizon_months": {"type": "integer"},
        "rating": {"type": "string", "enum": RATINGS},
        "confidence_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason_codes": {
            "type": "array",
            "items": {"type": "string", "enum": REASON_CODES},
            "minItems": 1,
            "maxItems": 5,
        },
        "thesis": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "category": {"type": "string"},
                    "publication_date": {"type": ["string", "null"]},
                    "source_url": {"type": ["string", "null"]},
                },
                "required": ["claim", "category", "publication_date", "source_url"],
            },
        },
        "analyst_opinions_encountered": {"type": "boolean"},
        "analyst_opinions_used": {"type": "boolean"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "symbol",
        "as_of",
        "horizon_months",
        "rating",
        "confidence_0_100",
        "reason_codes",
        "thesis",
        "risks",
        "evidence",
        "analyst_opinions_encountered",
        "analyst_opinions_used",
        "limitations",
    ],
}


def validate_assessment(payload: dict[str, Any]) -> None:
    missing = set(ASSESSMENT_SCHEMA["required"]) - set(payload)
    if missing:
        raise ValueError(f"Assessment is missing fields: {sorted(missing)}")
    if payload["rating"] not in RATING_SCORE:
        raise ValueError(f"Invalid rating: {payload['rating']}")
    if not 0 <= int(payload["confidence_0_100"]) <= 100:
        raise ValueError("confidence_0_100 must be between 0 and 100")
    if len(payload["thesis"]) != 3 or len(payload["risks"]) != 3:
        raise ValueError("Assessment must contain exactly three thesis points and three risks")


def _extract_urls(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and item.startswith(("https://", "http://")):
            found.add(item)

    visit(value)
    return sorted(found)


@dataclass
class AssessmentResult:
    assessment: dict[str, Any]
    metadata: dict[str, Any]
    raw: dict[str, Any]


class OpenAIResponsesClient:
    def __init__(self, api_key: str, model: str, reasoning_effort: str = "low"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai backend")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before using the OpenAI backend") from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def assess(self, prompt: str, allow_web: bool) -> AssessmentResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "stock_assessment",
                    "strict": True,
                    "schema": ASSESSMENT_SCHEMA,
                }
            },
        }
        if allow_web:
            kwargs["tools"] = [{"type": "web_search"}]
            kwargs["include"] = ["web_search_call.action.sources"]
        response = self.client.responses.create(**kwargs)
        payload = json.loads(response.output_text)
        validate_assessment(payload)
        raw = response.model_dump(mode="json")
        usage = raw.get("usage") or {}
        return AssessmentResult(
            assessment=payload,
            metadata={
                "backend": "OPENAI_RESPONSES_API",
                "model": self.model,
                "web_search_enabled": allow_web,
                "response_id": response.id,
                "retrieved_source_urls": _extract_urls(raw),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
            raw=raw,
        )


class DryRunClient:
    """Deterministic plumbing validator. Its output is not an AI recommendation."""

    backend_label = "DRY_RUN_NOT_AI"

    @staticmethod
    def _bounded(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return max(-1.0, min(1.0, number))

    def assess(
        self,
        symbol: str,
        as_of: str,
        horizon_months: int,
        condition: str,
        features: dict[str, Any],
    ) -> AssessmentResult:
        ret_12m = self._bounded(features.get("return_12m"))
        rel_spy = self._bounded(features.get("relative_return_vs_spy_12m"))
        dist_50 = self._bounded(features.get("distance_from_sma50"))
        dist_200 = self._bounded(features.get("distance_from_sma200"))
        volatility = max(0.0, self._bounded(features.get("volatility_60d_annualized")))

        if condition == "A_MEMORY_ONLY":
            continuous_score = 0.0
            confidence = 15
            codes = ["INSUFFICIENT_DATA", "UNCERTAINTY"]
            thesis = [
                "The company identity reached the prompt successfully.",
                "No dated market observations were supplied in this condition.",
                "The neutral label is a deterministic fallback for a plumbing test.",
            ]
        elif condition == "B_PRICE_HISTORY":
            continuous_score = 4.0 * ret_12m + 1.5 * dist_50
            confidence = 35
            codes = ["MOMENTUM", "TREND", "UNCERTAINTY"]
            thesis = [
                f"The trailing return signal used by the dry-run rule is {ret_12m:.1%}.",
                f"The distance from the 50-day average is {dist_50:.1%}.",
                "This condition intentionally excludes fundamentals and current news.",
            ]
        else:
            continuous_score = (
                3.0 * rel_spy + 1.5 * dist_50 + 1.0 * dist_200 - 0.5 * max(0.0, volatility - 0.35)
            )
            confidence = 50 if condition == "C_QUANTITATIVE" else 45
            codes = ["RELATIVE_STRENGTH", "TREND", "VOLATILITY"]
            thesis = [
                f"The 12-month relative-return signal versus SPY is {rel_spy:.1%}.",
                f"Price is {dist_50:.1%} from its 50-day average and {dist_200:.1%} from its 200-day average.",
                f"The 60-day annualized volatility signal is {volatility:.1%}.",
            ]

        rating = score_to_rating(continuous_score) or "HOLD"
        risks = [
            "This output comes from a fixed deterministic rule, not an AI model.",
            "The no-key price feed is an unofficial prototype source.",
            "No live analyst consensus or point-in-time fundamentals were available.",
        ]
        if condition == "D_CURRENT_WEB":
            risks[0] = "Web search is not executed by the dry-run backend, so Condition D is incomplete."
        payload = {
            "symbol": symbol,
            "as_of": as_of,
            "horizon_months": horizon_months,
            "rating": rating,
            "confidence_0_100": confidence,
            "reason_codes": codes,
            "thesis": thesis,
            "risks": risks,
            "evidence": [
                {
                    "claim": "Deterministic feature routing completed.",
                    "category": "PIPELINE_VALIDATION_ONLY",
                    "publication_date": as_of,
                    "source_url": None,
                }
            ],
            "analyst_opinions_encountered": False,
            "analyst_opinions_used": False,
            "limitations": [
                "DRY_RUN_NOT_AI: do not interpret this classification as investment advice or model evidence."
            ],
        }
        validate_assessment(payload)
        return AssessmentResult(
            assessment=payload,
            metadata={
                "backend": self.backend_label,
                "model": None,
                "web_search_enabled": False,
                "continuous_rule_score": round(continuous_score, 6),
                "retrieved_source_urls": [],
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            raw={"dry_run": True, "continuous_rule_score": continuous_score},
        )

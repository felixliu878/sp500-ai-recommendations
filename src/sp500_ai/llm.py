from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

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
EVIDENCE_CATEGORIES = REASON_CODES + [
    "PRICE_HISTORY",
    "BENCHMARK",
    "INDUSTRY",
    "REGULATORY",
    "MANAGEMENT_GUIDANCE",
    "OTHER",
]
EVIDENCE_ORIGINS = [
    "SUPPLIED_PRICE",
    "SUPPLIED_TECHNICAL",
    "SUPPLIED_FUNDAMENTAL",
    "WEB_PRIMARY",
    "WEB_SECONDARY",
    "MODEL_MEMORY",
    "OTHER",
]
WEB_EVIDENCE_ORIGINS = {"WEB_PRIMARY", "WEB_SECONDARY"}
SUPPLIED_EVIDENCE_ORIGINS = {
    "SUPPLIED_PRICE",
    "SUPPLIED_TECHNICAL",
    "SUPPLIED_FUNDAMENTAL",
}
ALLOWED_EVIDENCE_ORIGINS_BY_CONDITION = {
    # Company identity and sector have no dedicated evidence-origin enum.
    "A_MEMORY_ONLY": {"MODEL_MEMORY", "OTHER", "SUPPLIED_FUNDAMENTAL"},
    "B_PRICE_HISTORY": {"SUPPLIED_PRICE", "OTHER"},
    "C_QUANTITATIVE": SUPPLIED_EVIDENCE_ORIGINS | {"OTHER"},
    "D_CURRENT_WEB": (
        SUPPLIED_EVIDENCE_ORIGINS | WEB_EVIDENCE_ORIGINS | {"OTHER"}
    ),
}


ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "symbol": {"type": "string"},
        "as_of": {"type": "string"},
        "horizon_months": {"type": "integer", "minimum": 1},
        "rating": {"type": "string", "enum": RATINGS},
        "confidence_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
        "evidence_sufficiency": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH"],
        },
        "forced_rating_due_to_insufficient_data": {"type": "boolean"},
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
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "category": {"type": "string", "enum": EVIDENCE_CATEGORIES},
                    "origin": {"type": "string", "enum": EVIDENCE_ORIGINS},
                    "observation_date": {"type": ["string", "null"]},
                    "publication_date": {"type": ["string", "null"]},
                    "source_url": {"type": ["string", "null"]},
                },
                "required": [
                    "claim",
                    "category",
                    "origin",
                    "observation_date",
                    "publication_date",
                    "source_url",
                ],
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
        "evidence_sufficiency",
        "forced_rating_due_to_insufficient_data",
        "reason_codes",
        "thesis",
        "risks",
        "evidence",
        "analyst_opinions_encountered",
        "analyst_opinions_used",
        "limitations",
    ],
}


def _parse_evidence_date(
    value: Any, field_name: str, cutoff: date | None
) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string or null")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format")
    if cutoff is not None and parsed > cutoff:
        raise ValueError(
            f"{field_name} {value} is after the assessment cutoff {cutoff.isoformat()}"
        )
    return parsed


def validate_assessment(
    payload: dict[str, Any],
    *,
    cutoff: str | date | None = None,
    allow_web: bool | None = None,
    condition: str | None = None,
) -> None:
    missing = set(ASSESSMENT_SCHEMA["required"]) - set(payload)
    if missing:
        raise ValueError(f"Assessment is missing fields: {sorted(missing)}")
    if payload["rating"] not in RATING_SCORE:
        raise ValueError(f"Invalid rating: {payload['rating']}")
    horizon_months = payload["horizon_months"]
    if (
        isinstance(horizon_months, bool)
        or not isinstance(horizon_months, int)
        or horizon_months < 1
    ):
        raise ValueError("horizon_months must be a positive integer")
    if not 0 <= int(payload["confidence_0_100"]) <= 100:
        raise ValueError("confidence_0_100 must be between 0 and 100")
    if payload["evidence_sufficiency"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("evidence_sufficiency must be LOW, MEDIUM, or HIGH")
    if len(payload["thesis"]) != 3 or len(payload["risks"]) != 3:
        raise ValueError("Assessment must contain exactly three thesis points and three risks")
    if bool(payload["analyst_opinions_used"]) and not bool(
        payload["analyst_opinions_encountered"]
    ):
        raise ValueError(
            "analyst_opinions_used cannot be true when analyst_opinions_encountered is false"
        )

    cutoff_value = cutoff if cutoff is not None else payload.get("as_of")
    if isinstance(cutoff_value, date):
        cutoff_date = cutoff_value
    else:
        try:
            cutoff_date = date.fromisoformat(str(cutoff_value))
        except (TypeError, ValueError) as exc:
            raise ValueError("Assessment cutoff must use YYYY-MM-DD format") from exc
        if cutoff_date.isoformat() != str(cutoff_value):
            raise ValueError("Assessment cutoff must use YYYY-MM-DD format")

    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be an array")
    allowed_origins = None
    if condition is not None:
        allowed_origins = ALLOWED_EVIDENCE_ORIGINS_BY_CONDITION.get(condition)
        if allowed_origins is None:
            raise ValueError(f"Unknown assessment condition: {condition}")
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError(f"evidence[{index}] must be an object")
        origin = item.get("origin")
        if origin not in EVIDENCE_ORIGINS:
            raise ValueError(f"evidence[{index}].origin is invalid: {origin}")
        if allowed_origins is not None and origin not in allowed_origins:
            raise ValueError(
                f"evidence[{index}].origin {origin} is not permitted in {condition}"
            )
        category = item.get("category")
        if category not in EVIDENCE_CATEGORIES:
            raise ValueError(f"evidence[{index}].category is invalid: {category}")
        observation_date = _parse_evidence_date(
            item.get("observation_date"),
            f"evidence[{index}].observation_date",
            cutoff_date,
        )
        publication_date = _parse_evidence_date(
            item.get("publication_date"),
            f"evidence[{index}].publication_date",
            cutoff_date,
        )
        source_url = item.get("source_url")
        if source_url is not None and not isinstance(source_url, str):
            raise ValueError(f"evidence[{index}].source_url must be a string or null")

        if origin in WEB_EVIDENCE_ORIGINS:
            if allow_web is False:
                raise ValueError("Web evidence is not permitted in this condition")
            if publication_date is None:
                raise ValueError(
                    f"evidence[{index}] with a web origin requires publication_date"
                )
            parsed_url = urlparse(source_url or "")
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ValueError(
                    f"evidence[{index}] with a web origin requires an HTTP(S) source_url"
                )
        elif source_url is not None:
            raise ValueError(
                f"evidence[{index}] may only include source_url with a web origin"
            )

        if origin in SUPPLIED_EVIDENCE_ORIGINS:
            if observation_date is None:
                raise ValueError(
                    f"evidence[{index}] with a supplied-data origin requires observation_date"
                )
            if publication_date is not None:
                raise ValueError(
                    f"evidence[{index}] with a supplied-data origin must use publication_date=null"
                )


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


WEB_ANALYST_TERMS = (
    "analyst",
    "analyst rating",
    "analyst ratings",
    "price target",
    "consensus",
    "upgrade",
    "downgrade",
    "overweight",
    "underweight",
    "tipranks",
    "marketbeat",
    "zacks",
)


def _audit_web_actions(raw: dict[str, Any]) -> dict[str, Any]:
    web_calls = [
        item for item in raw.get("output", []) if item.get("type") == "web_search_call"
    ]
    actions = [item.get("action") or {} for item in web_calls]
    source_count = sum(len(action.get("sources") or []) for action in actions)
    action_text = json.dumps(actions, sort_keys=True).lower()
    matched_terms = [
        term
        for term in WEB_ANALYST_TERMS
        if re.search(rf"\b{re.escape(term)}\b", action_text)
    ]
    return {
        "web_search_call_count": len(web_calls),
        "web_search_source_count": source_count,
        "analyst_terms_detected_in_web_actions": bool(matched_terms),
        "analyst_web_action_matched_terms": matched_terms,
    }


@dataclass
class AssessmentResult:
    assessment: dict[str, Any]
    metadata: dict[str, Any]
    raw: dict[str, Any]


class AssessmentResponseError(RuntimeError):
    """A paid response failed validation after the API returned an auditable payload."""

    def __init__(
        self,
        message: str,
        *,
        raw: dict[str, Any],
        metadata: dict[str, Any],
    ):
        super().__init__(message)
        self.raw = raw
        self.metadata = metadata


class OpenAIResponsesClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: str = "low",
        max_output_tokens: int = 3000,
        web_max_tool_calls: int = 4,
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai backend")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if (
            isinstance(web_max_tool_calls, bool)
            or not isinstance(web_max_tool_calls, int)
            or web_max_tool_calls < 1
        ):
            raise ValueError("web_max_tool_calls must be a positive integer")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before using the OpenAI backend") from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.web_max_tool_calls = web_max_tool_calls

    def assess(
        self,
        prompt: str,
        allow_web: bool,
        condition: str | None = None,
    ) -> AssessmentResult:
        if not isinstance(allow_web, bool):
            raise ValueError("allow_web must be boolean")
        if condition is not None:
            if condition not in ALLOWED_EVIDENCE_ORIGINS_BY_CONDITION:
                raise ValueError(f"Unknown assessment condition: {condition}")
            expected_web = condition == "D_CURRENT_WEB"
            if allow_web != expected_web:
                raise ValueError(
                    f"allow_web={allow_web} is inconsistent with {condition}"
                )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
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
            kwargs["max_tool_calls"] = self.web_max_tool_calls
        response = self.client.responses.create(**kwargs)
        raw = response.model_dump(mode="json")
        usage = raw.get("usage") or {}
        web_audit = _audit_web_actions(raw)
        metadata = {
            "backend": "OPENAI_RESPONSES_API",
            "model": raw.get("model") or self.model,
            "requested_model": self.model,
            "web_search_enabled": allow_web,
            "response_id": response.id,
            "retrieved_source_urls": _extract_urls(raw),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            **web_audit,
        }
        if not response.output_text:
            refusal = next(
                (
                    item.refusal
                    for output in response.output
                    if getattr(output, "type", None) == "message"
                    for item in getattr(output, "content", [])
                    if getattr(item, "type", None) == "refusal"
                ),
                None,
            )
            raise AssessmentResponseError(
                f"Model returned no structured assessment. Refusal: {refusal}",
                raw=raw,
                metadata=metadata,
            )
        try:
            payload = json.loads(response.output_text)
            validate_assessment(
                payload,
                cutoff=payload.get("as_of"),
                allow_web=allow_web,
                condition=condition,
            )
        except Exception as exc:
            raise AssessmentResponseError(
                f"Structured assessment validation failed: {type(exc).__name__}: {exc}",
                raw=raw,
                metadata=metadata,
            ) from exc
        return AssessmentResult(
            assessment=payload,
            metadata=metadata,
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
            evidence_sufficiency = "LOW"
            forced_rating = True
            codes = ["INSUFFICIENT_DATA", "UNCERTAINTY"]
            thesis = [
                "The company identity reached the prompt successfully.",
                "No dated market observations were supplied in this condition.",
                "The neutral label is a deterministic fallback for a plumbing test.",
            ]
        elif condition == "B_PRICE_HISTORY":
            continuous_score = 4.0 * ret_12m + 1.5 * dist_50
            confidence = 35
            evidence_sufficiency = "MEDIUM"
            forced_rating = False
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
            evidence_sufficiency = "HIGH" if condition == "C_QUANTITATIVE" else "MEDIUM"
            forced_rating = condition == "D_CURRENT_WEB"
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
        evidence_origin = {
            "A_MEMORY_ONLY": "MODEL_MEMORY",
            "B_PRICE_HISTORY": "SUPPLIED_PRICE",
        }.get(condition, "SUPPLIED_TECHNICAL")
        payload = {
            "symbol": symbol,
            "as_of": as_of,
            "horizon_months": horizon_months,
            "rating": rating,
            "confidence_0_100": confidence,
            "evidence_sufficiency": evidence_sufficiency,
            "forced_rating_due_to_insufficient_data": forced_rating,
            "reason_codes": codes,
            "thesis": thesis,
            "risks": risks,
            "evidence": [
                {
                    "claim": "Deterministic feature routing completed.",
                    "category": "OTHER",
                    "origin": evidence_origin,
                    "observation_date": (
                        None if evidence_origin == "MODEL_MEMORY" else as_of
                    ),
                    "publication_date": None,
                    "source_url": None,
                }
            ],
            "analyst_opinions_encountered": False,
            "analyst_opinions_used": False,
            "limitations": [
                "DRY_RUN_NOT_AI: do not interpret this classification as investment advice or model evidence."
            ],
        }
        validate_assessment(
            payload,
            cutoff=as_of,
            allow_web=False,
            condition=condition,
        )
        return AssessmentResult(
            assessment=payload,
            metadata={
                "backend": self.backend_label,
                "model": None,
                "requested_model": None,
                "web_search_enabled": False,
                "continuous_rule_score": round(continuous_score, 6),
                "retrieved_source_urls": [],
                "web_search_call_count": 0,
                "web_search_source_count": 0,
                "analyst_terms_detected_in_web_actions": False,
                "analyst_web_action_matched_terms": [],
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            raw={"dry_run": True, "continuous_rule_score": continuous_score},
        )

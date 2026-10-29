import unittest

from sp500_ai.llm import (
    ASSESSMENT_SCHEMA,
    DryRunClient,
    OpenAIResponsesClient,
    _audit_web_actions,
    validate_assessment,
)


def valid_assessment(
    *,
    origin: str = "SUPPLIED_PRICE",
    observation_date: str | None = "2026-07-24",
    publication_date: str | None = None,
    source_url: str | None = None,
    horizon_months: int = 12,
) -> dict:
    return {
        "symbol": "MU",
        "as_of": "2026-07-24",
        "horizon_months": horizon_months,
        "rating": "HOLD",
        "confidence_0_100": 50,
        "evidence_sufficiency": "MEDIUM",
        "forced_rating_due_to_insufficient_data": False,
        "reason_codes": ["UNCERTAINTY"],
        "thesis": ["One", "Two", "Three"],
        "risks": ["One", "Two", "Three"],
        "evidence": [
            {
                "claim": "Test evidence.",
                "category": "OTHER",
                "origin": origin,
                "observation_date": observation_date,
                "publication_date": publication_date,
                "source_url": source_url,
            }
        ],
        "analyst_opinions_encountered": False,
        "analyst_opinions_used": False,
        "limitations": [],
    }


class LLMTests(unittest.TestCase):
    def test_schema_disallows_extra_properties(self):
        self.assertFalse(ASSESSMENT_SCHEMA["additionalProperties"])
        self.assertEqual(
            ASSESSMENT_SCHEMA["properties"]["horizon_months"]["minimum"], 1
        )
        evidence_required = ASSESSMENT_SCHEMA["properties"]["evidence"]["items"]["required"]
        self.assertIn("origin", evidence_required)
        self.assertIn("observation_date", evidence_required)

    def test_condition_specific_evidence_origins_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "not permitted"):
            validate_assessment(
                valid_assessment(
                    origin="MODEL_MEMORY",
                    observation_date=None,
                ),
                cutoff="2026-07-24",
                allow_web=False,
                condition="B_PRICE_HISTORY",
            )
        validate_assessment(
            valid_assessment(
                origin="MODEL_MEMORY",
                observation_date=None,
            ),
            cutoff="2026-07-24",
            allow_web=False,
            condition="A_MEMORY_ONLY",
        )

    def test_web_evidence_requires_publication_date(self):
        with self.assertRaisesRegex(ValueError, "requires publication_date"):
            validate_assessment(
                valid_assessment(
                    origin="WEB_PRIMARY",
                    observation_date=None,
                    publication_date=None,
                    source_url="https://example.com/filing",
                ),
                cutoff="2026-07-24",
                allow_web=True,
                condition="D_CURRENT_WEB",
            )
        validate_assessment(
            valid_assessment(
                origin="WEB_PRIMARY",
                observation_date=None,
                publication_date="2026-07-23",
                source_url="https://example.com/filing",
            ),
            cutoff="2026-07-24",
            allow_web=True,
            condition="D_CURRENT_WEB",
        )

    def test_zero_horizon_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "horizon_months"):
            validate_assessment(
                valid_assessment(horizon_months=0),
                cutoff="2026-07-24",
                allow_web=False,
                condition="B_PRICE_HISTORY",
            )

    def test_openai_client_rejects_invalid_output_limit_before_setup(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_output_tokens"):
                    OpenAIResponsesClient(
                        "dummy-key",
                        "test-model",
                        max_output_tokens=value,
                    )

    def test_openai_client_rejects_invalid_web_tool_limit_before_setup(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "web_max_tool_calls"):
                    OpenAIResponsesClient(
                        "dummy-key",
                        "test-model",
                        web_max_tool_calls=value,
                    )

    def test_openai_client_rejects_condition_tool_mismatch_before_call(self):
        client = object.__new__(OpenAIResponsesClient)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            client.assess(
                "test prompt",
                allow_web=True,
                condition="C_QUANTITATIVE",
            )

    def test_web_action_audit_detects_analyst_query(self):
        raw = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "query": "MU analyst price target",
                        "sources": [{"type": "url", "url": "https://example.com"}],
                    },
                }
            ]
        }
        audit = _audit_web_actions(raw)
        self.assertTrue(audit["analyst_terms_detected_in_web_actions"])
        self.assertEqual(audit["web_search_call_count"], 1)
        self.assertEqual(audit["web_search_source_count"], 1)

    def test_web_action_audit_ignores_generic_rating_metadata(self):
        raw = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "query": "utility filing as of the rating date",
                        "sources": [
                            {
                                "type": "url",
                                "url": "https://example.com/filing",
                                "title": "As of the rating date",
                            }
                        ],
                    },
                }
            ]
        }
        audit = _audit_web_actions(raw)
        self.assertFalse(audit["analyst_terms_detected_in_web_actions"])
        self.assertEqual(audit["analyst_web_action_matched_terms"], [])

    def test_dry_run_is_unambiguously_not_ai(self):
        features = {
            "return_12m": 0.1,
            "relative_return_vs_spy_12m": 0.02,
            "distance_from_sma50": 0.01,
            "distance_from_sma200": 0.03,
            "volatility_60d_annualized": 0.25,
        }
        for condition in (
            "A_MEMORY_ONLY",
            "B_PRICE_HISTORY",
            "C_QUANTITATIVE",
            "D_CURRENT_WEB",
        ):
            with self.subTest(condition=condition):
                result = DryRunClient().assess(
                    "MU", "2026-07-17", 12, condition, features
                )
                validate_assessment(
                    result.assessment,
                    cutoff="2026-07-17",
                    allow_web=False,
                    condition=condition,
                )
                self.assertEqual(result.metadata["backend"], "DRY_RUN_NOT_AI")
                self.assertFalse(result.metadata["web_search_enabled"])
                self.assertEqual(len(result.assessment["thesis"]), 3)
                self.assertEqual(len(result.assessment["risks"]), 3)


if __name__ == "__main__":
    unittest.main()

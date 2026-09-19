import unittest

from app.contracts import (
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    Concern,
    DialogueTurn,
    Evidence,
    Speaker,
    validate_analysis_evidence,
)
from app.providers import (
    ProviderError,
    _nvidia_chat_url,
    _nvidia_payload,
    _parse_nvidia_response,
    mock_analysis,
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.request = AnalysisRequest(
            session_id="demo-001",
            turns=[
                DialogueTurn(
                    turn_id="t1",
                    speaker=Speaker.officer,
                    text="If you confess, I can make sure you go home tonight.",
                )
            ],
        )

    def test_mock_analysis_returns_exact_evidence(self):
        response = mock_analysis(self.request)
        validate_analysis_evidence(self.request, response)
        self.assertEqual(response.status, AnalysisStatus.ok)
        self.assertEqual(len(response.concerns), 1)
        self.assertEqual(response.concerns[0].category, "benefit_for_confession")
        self.assertEqual(response.concerns[0].evidence[0].quote, self.request.turns[0].text)

    def test_ordinary_questioning_returns_valid_negative(self):
        request = AnalysisRequest(
            session_id="demo-002",
            turns=[
                DialogueTurn(
                    turn_id="t1",
                    speaker=Speaker.officer,
                    text="Where were you yesterday afternoon?",
                )
            ],
        )
        response = mock_analysis(request)
        self.assertEqual(response.status, AnalysisStatus.ok)
        self.assertEqual(response.concerns, [])

    def test_non_verbatim_evidence_is_rejected(self):
        response = AnalysisResponse(
            session_id=self.request.session_id,
            status=AnalysisStatus.ok,
            concerns=[
                Concern(
                    concern_id="c1",
                    category="benefit_for_confession",
                    explanation="Potential inducement.",
                    alert_text="Review the statement.",
                    evidence=[
                        Evidence(
                            quote="This quote was never spoken.",
                            turn_id="t1",
                            speaker=Speaker.officer,
                        )
                    ],
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "not verbatim"):
            validate_analysis_evidence(self.request, response)

    def test_wrong_session_is_rejected(self):
        response = AnalysisResponse(
            session_id="old-session",
            status=AnalysisStatus.ok,
            concerns=[],
        )
        with self.assertRaisesRegex(ValueError, "session_id"):
            validate_analysis_evidence(self.request, response)

    def test_nvidia_base_url_becomes_chat_completions_url(self):
        self.assertEqual(
            _nvidia_chat_url("https://integrate.api.nvidia.com/v1"),
            "https://integrate.api.nvidia.com/v1/chat/completions",
        )

    def test_nvidia_payload_uses_selected_model_and_session(self):
        payload = _nvidia_payload(
            self.request, "nvidia/nemotron-3.5-lightning-30b-a3b"
        )
        self.assertEqual(
            payload["model"], "nvidia/nemotron-3.5-lightning-30b-a3b"
        )
        self.assertIn("demo-001", payload["messages"][1]["content"])
        self.assertEqual(
            payload["chat_template_kwargs"], {"enable_thinking": False}
        )

    def test_valid_nvidia_chat_response_is_parsed(self):
        response = _parse_nvidia_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": """```json
{
  "decisions": [
    {"turn_id": "t1", "category": "none"}
  ]
}
```"""
                        }
                    }
                ]
            },
            self.request,
        )
        self.assertEqual(response.session_id, "demo-001")
        self.assertEqual(response.concerns, [])

    def test_empty_nvidia_decisions_are_valid_negative(self):
        response = _parse_nvidia_response(
            {
                "choices": [
                    {"message": {"content": '{"decisions":[]}'}}
                ]
            },
            self.request,
        )
        self.assertEqual(response.status, AnalysisStatus.ok)
        self.assertEqual(response.concerns, [])

    def test_malformed_nvidia_chat_response_is_rejected(self):
        with self.assertRaises(ProviderError) as raised:
            _parse_nvidia_response(
                {"choices": [{"message": {"content": "not json"}}]},
                self.request,
            )
        self.assertEqual(raised.exception.code, "ANALYSIS_INVALID")

    def test_nvidia_benefit_decision_builds_exact_evidence(self):
        response = _parse_nvidia_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"decisions":[{"turn_id":"t1",'
                                '"category":"benefit_for_confession"}]}'
                            )
                        }
                    }
                ]
            },
            self.request,
        )
        validate_analysis_evidence(self.request, response)
        self.assertEqual(len(response.concerns), 1)
        self.assertEqual(
            response.concerns[0].evidence[0].quote,
            self.request.turns[0].text,
        )


if __name__ == "__main__":
    unittest.main()

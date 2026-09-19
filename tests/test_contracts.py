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
from app.providers import mock_analysis


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


if __name__ == "__main__":
    unittest.main()


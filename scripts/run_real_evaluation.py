"""Run a small, synthetic, non-legal-validation evaluation against NVIDIA."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path

from steelhacks_reasoning.analyzer import DialogueAnalyzer
from steelhacks_reasoning.client import NvidiaNemotronClient
from steelhacks_reasoning.models import AnalyzeRequest
from steelhacks_reasoning.validation import ModelOutputInvalid, parse_and_validate_model_output


class RecordingClient:
    def __init__(self, client: NvidiaNemotronClient) -> None:
        self.client = client
        self.outputs: list[str] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        output = self.client.complete(messages)
        self.outputs.append(output)
        return output


def make_timing_recorder(timings: dict[str, float]) -> Callable[[str, float], None]:
    def record_timing(stage: str, elapsed: float) -> None:
        timings[stage] = round(elapsed, 4)

    return record_timing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", help="Optional case IDs to run")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "tests/fixtures/real_model_cases.json").read_text())
    if args.cases:
        wanted = set(args.cases)
        cases = [case for case in cases if case["case_id"] in wanted]
    if not cases:
        raise SystemExit("No matching evaluation cases.")

    results: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        request = AnalyzeRequest.model_validate(
            {
                "session_id": "synthetic-evaluation",
                "request_id": f"eval-{case['case_id']}",
                "sequence_number": index,
                "context_turns": case["context_turns"],
                "new_turns": case["new_turns"],
            }
        )
        stage_timings: dict[str, float] = {}
        record_timing = make_timing_recorder(stage_timings)
        client = RecordingClient(NvidiaNemotronClient())
        started = time.perf_counter()
        response = DialogueAnalyzer(client, timing_callback=record_timing).analyze_dialogue(request)
        total_latency = time.perf_counter() - started

        first_json_valid = False
        first_evidence_valid = False
        first_validation_error = None
        if client.outputs:
            raw = client.outputs[0]
            try:
                json.loads(raw)
                first_json_valid = True
            except json.JSONDecodeError:
                pass
            try:
                parse_and_validate_model_output(raw, request)
                first_evidence_valid = True
            except ModelOutputInvalid as exc:
                first_validation_error = str(exc)

        expected = case["expected_category"]
        actual = response.concerns[0].category.value if response.concerns else None
        error_code = response.error.code.value if response.error else None
        transport_succeeded = bool(client.outputs)
        classification_completed = response.error is None
        detected_evidence_valid: bool | None = None
        if response.concerns:
            detected_evidence_valid = all(concern.evidence for concern in response.concerns)

        results.append(
            {
                "case_id": case["case_id"],
                "expected": expected,
                "actual": actual,
                "classification_correct": classification_completed and expected == actual,
                "transport_succeeded": transport_succeeded,
                "first_response_json_valid": first_json_valid,
                "first_response_evidence_valid": first_evidence_valid,
                "first_response_validation_error": first_validation_error,
                "final_response_json_valid": bool(json.loads(response.model_dump_json())),
                "detected_evidence_valid": detected_evidence_valid,
                "repair_call_required": "repair_model_call" in stage_timings,
                "stage_timings_seconds": stage_timings,
                "total_latency_seconds": round(total_latency, 3),
                "error": error_code,
            }
        )

    completed = [item for item in results if item["error"] is None]
    transported = [item for item in results if item["transport_succeeded"]]
    false_positives = sum(
        item["expected"] is None and item["actual"] is not None for item in completed
    )
    false_negatives = sum(
        item["expected"] is not None and item["actual"] is None for item in completed
    )
    latencies = [
        value for item in results if isinstance((value := item["total_latency_seconds"]), float)
    ]
    timeout_count = sum(item["error"] == "MODEL_TIMEOUT" for item in results)
    detected = [item for item in completed if item["actual"]]
    report = {
        "label": "small synthetic evaluation; not legal validation",
        "cases": results,
        "transport_timeout_rate": timeout_count / len(results),
        "first_response_valid_json_rate_among_transport_successes": (
            sum(bool(item["first_response_json_valid"]) for item in transported) / len(transported)
            if transported
            else 0.0
        ),
        "final_response_valid_json_rate": sum(
            bool(item["final_response_json_valid"]) for item in results
        )
        / len(results),
        "detected_concern_evidence_validation_rate": (
            sum(item["detected_evidence_valid"] is True for item in detected) / len(detected)
            if detected
            else None
        ),
        "false_positives_among_completed": false_positives,
        "false_negatives_among_completed": false_negatives,
        "median_latency_seconds": round(statistics.median(latencies), 3),
        "maximum_latency_seconds": round(max(latencies), 3),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_exposes_continuous_capture_controls():
    response = client.get("/")
    assert response.status_code == 200
    assert "Start live session" in response.text
    assert "Finalize turn now" in response.text
    assert "suspectDeviceSelect" in response.text
    assert "officerDeviceSelect" in response.text
    assert "suspectMeterFill" in response.text
    assert "officerMeterFill" in response.text


def test_home_exposes_navigation_and_archive_controls():
    response = client.get("/")
    assert response.status_code == 200
    assert "Live Interrogation Monitor" in response.text
    assert "Past Interrogation Archives" in response.text
    assert 'id="archiveSearch"' in response.text
    assert 'id="archiveSessionSelect"' in response.text
    assert "Show concern lines only" in response.text
    assert "Download JSON" in response.text
    assert "Download CSV" in response.text
    assert 'id="saveSessionDialog"' in response.text


def test_health_reports_mock_modes():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["audio_provider_mode"] == "mock"
    assert body["analysis_provider_mode"] == "mock"


def test_transcript_is_idempotent_for_identical_audio():
    files = {"file": ("clip.webm", b"fake-audio", "audio/webm")}
    form = {"turn_id": "test-turn-same", "duration_ms": "1000"}
    first = client.post("/api/audio/transcribe", files=files, data=form)
    second = client.post("/api/audio/transcribe", files=files, data=form)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_reused_turn_id_with_different_audio_is_rejected():
    form = {"turn_id": "test-turn-conflict", "duration_ms": "1000"}
    first = client.post(
        "/api/audio/transcribe",
        files={"file": ("clip.webm", b"audio-one", "audio/webm")},
        data=form,
    )
    second = client.post(
        "/api/audio/transcribe",
        files={"file": ("clip.webm", b"audio-two", "audio/webm")},
        data=form,
    )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "TURN_ID_CONFLICT"


def test_mock_analysis_and_synthesis():
    analysis = client.post(
        "/api/analyze",
        json={
            "session_id": "test-session",
            "turns": [
                {
                    "turn_id": "t1",
                    "speaker": "officer",
                    "text": "If you confess, I can make sure you go home tonight.",
                    "timestamp_ms": None,
                }
            ],
        },
    )
    assert analysis.status_code == 200
    concern = analysis.json()["concerns"][0]
    assert "You have the right to remain silent" in concern["alert_text"]
    assert "right to speak with an attorney" not in concern["alert_text"]
    assert "not legal advice" not in concern["alert_text"]
    speech = client.post(
        "/api/audio/synthesize",
        json={
            "concern_id": concern["concern_id"],
            "text": concern["alert_text"],
            "voice": "default",
        },
    )
    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/wav")
    assert speech.content.startswith(b"RIFF")

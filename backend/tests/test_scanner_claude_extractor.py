"""
Coverage for scanner/claude_extractor.py's response parsing — in
particular the real bug found via a live call against the actual
Anthropic API (not assumed, not simulated until it happened for real):
this account/model returns a "thinking" content block before the "text"
block when extended thinking is active, so content[0]["text"] raised
KeyError. Fixed to find the text block(s) by type instead of assuming
position 0; these tests lock that fix in against ever regressing back to
the position-based assumption.
"""
import requests

import scanner.claude_extractor as claude_extractor
from scanner.claude_extractor import ScanExtractionError, extract_job_detail, extract_jobs


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def _messages_payload(content_blocks):
    return {"model": "claude-sonnet-5", "content": content_blocks, "type": "message", "role": "assistant"}


def test_extracts_jobs_when_a_thinking_block_precedes_the_text_block(monkeypatch):
    """The exact real-world shape that broke in production: content[0]
    is a thinking block with no "text" key at all, content[1] is the
    actual text block."""
    payload = _messages_payload([
        {"type": "thinking", "thinking": "...", "signature": "..."},
        {"type": "text", "text": '[{"title": "Office Assistant", "company_name": null, "location": "Freetown", "salary": null, "description": null, "apply_url": null, "external_id": null, "employment_type": "Full-time"}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Office Assistant"
    assert jobs[0]["employment_type"] == "Full-time"


def test_extracts_a_deadline_field(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '[{"title": "Enumerator", "company_name": null, "location": null, "salary": null, "description": null, "apply_url": null, "external_id": null, "employment_type": null, "deadline": "2026-08-23"}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert jobs[0]["deadline"] == "2026-08-23"


def test_deadline_defaults_to_null_when_the_field_is_absent(monkeypatch):
    """A record that doesn't include "deadline" at all (e.g. an older
    cached response, or Claude just omitting a null field) must not
    raise a KeyError -- JOB_FIELDS pulls it via record.get(), same as
    every other optional field."""
    payload = _messages_payload([
        {"type": "text", "text": '[{"title": "Job With No Deadline Key", "company_name": null, "location": null, "salary": null, "description": null, "apply_url": null, "external_id": null}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert jobs[0]["deadline"] is None


def test_extract_job_detail_extracts_a_deadline_field(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '{"description": null, "salary": null, "employment_type": null, "location": null, "deadline": "2026-09-01"}'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    detail = extract_job_detail("some markdown", api_key="fake-key")
    assert detail["deadline"] == "2026-09-01"


def test_extracts_jobs_when_text_is_the_only_block(monkeypatch):
    """The common case (no extended thinking) still works — the fix
    isn't position-dependent in either direction."""
    payload = _messages_payload([
        {"type": "text", "text": '[{"title": "Data Clerk", "company_name": null, "location": null, "salary": null, "description": null, "apply_url": null, "external_id": null}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Data Clerk"


def test_strips_markdown_code_fence_around_json(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '```json\n[{"title": "Fenced Job", "company_name": null, "location": null, "salary": null, "description": null, "apply_url": null, "external_id": null}]\n```'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert jobs[0]["title"] == "Fenced Job"


def test_drops_records_missing_a_title_but_keeps_the_rest(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '[{"title": "Good Job", "company_name": null, "location": null, "salary": null, "description": null, "apply_url": null, "external_id": null}, {"title": "", "company_name": "X"}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    jobs = extract_jobs("some markdown", api_key="fake-key")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Good Job"


def test_raises_when_no_text_block_exists_at_all(monkeypatch):
    """Only thinking/tool_use blocks, no text -- must fail loudly with a
    message naming what block types WERE present, not crash with a raw
    KeyError somewhere downstream."""
    payload = _messages_payload([{"type": "thinking", "thinking": "...", "signature": "..."}])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    try:
        extract_jobs("some markdown", api_key="fake-key")
        assert False, "expected ScanExtractionError"
    except ScanExtractionError as e:
        assert "thinking" in str(e)


def test_raises_on_malformed_json_in_text_block(monkeypatch):
    payload = _messages_payload([{"type": "text", "text": "not json at all"}])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    try:
        extract_jobs("some markdown", api_key="fake-key")
        assert False, "expected ScanExtractionError"
    except ScanExtractionError:
        pass


def test_raises_on_401(monkeypatch):
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(401, {}, "unauthorized"))
    try:
        extract_jobs("some markdown", api_key="bad-key")
        assert False, "expected ScanExtractionError"
    except ScanExtractionError as e:
        assert "401" in str(e)


def test_raises_when_api_key_missing():
    try:
        extract_jobs("some markdown", api_key=None)
        assert False, "expected ScanExtractionError"
    except ScanExtractionError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


def test_extract_job_detail_returns_a_single_object(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '{"description": "Full role description here.", "salary": "Negotiable", "employment_type": "Full-time", "location": "Freetown"}'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    detail = extract_job_detail("some markdown", api_key="fake-key")
    assert detail["description"] == "Full role description here."
    assert detail["salary"] == "Negotiable"
    assert detail["employment_type"] == "Full-time"
    assert detail["location"] == "Freetown"


def test_extract_job_detail_handles_a_thinking_block_too(monkeypatch):
    """Same real-world response shape as extract_jobs -- the shared
    _call_claude_for_text() helper is what actually protects this, not
    a second copy of the fix."""
    payload = _messages_payload([
        {"type": "thinking", "thinking": "...", "signature": "..."},
        {"type": "text", "text": '{"description": null, "salary": null, "employment_type": null, "location": null}'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    detail = extract_job_detail("some markdown", api_key="fake-key")
    assert detail == {"description": None, "salary": None, "employment_type": None, "location": None, "deadline": None}


def test_extract_job_detail_strips_markdown_code_fence(monkeypatch):
    payload = _messages_payload([
        {"type": "text", "text": '```json\n{"description": "Fenced.", "salary": null, "employment_type": null, "location": null}\n```'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    detail = extract_job_detail("some markdown", api_key="fake-key")
    assert detail["description"] == "Fenced."


def test_extract_job_detail_raises_when_response_is_an_array_not_an_object(monkeypatch):
    """A single detail page describes exactly one job -- if Claude
    answers with an array (e.g. it mistook the page for a listing
    index), that's a shape mismatch this caller must catch, not silently
    accept as one malformed record the way extract_jobs would."""
    payload = _messages_payload([
        {"type": "text", "text": '[{"description": "oops, array not object"}]'},
    ])
    monkeypatch.setattr(claude_extractor.requests, "post", lambda *a, **k: _FakeResponse(200, payload))

    try:
        extract_job_detail("some markdown", api_key="fake-key")
        assert False, "expected ScanExtractionError"
    except ScanExtractionError as e:
        assert "object" in str(e)


def test_extract_job_detail_raises_when_api_key_missing():
    try:
        extract_job_detail("some markdown", api_key=None)
        assert False, "expected ScanExtractionError"
    except ScanExtractionError as e:
        assert "ANTHROPIC_API_KEY" in str(e)

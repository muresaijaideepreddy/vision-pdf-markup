"""Offline checks of provider contracts and the untrusted response boundary."""

import base64
import copy
import io
import json
import urllib.error

import pytest

from markupdoc import vision
from markupdoc.schema import RESPONSE_SCHEMA, validate_response


@pytest.fixture(autouse=True)
def never_use_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must never create a real provider connection")

    monkeypatch.setattr(vision.urllib.request, "build_opener", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def inputs(tmp_path):
    scan = tmp_path / "marked.pdf"
    scan.write_bytes(b"%PDF-1.7\nsynthetic scan bytes; not a live input")
    images = [tmp_path / "page-001.png", tmp_path / "page-002.png"]
    for index, image in enumerate(images):
        image.write_bytes(b"synthetic PNG page " + str(index).encode())
    snapshot = {
        "sha256": "a" * 64,
        "blocks": [{"id": "word/document.xml:p000001", "text": "The cat was here.", "editable": True}],
        "warnings": ["Original document inspection warning"],
        # The request must use only the original's blocks and warnings.
        "reference": "HELD_OUT_HUMAN_FINAL_MUST_NOT_BE_SENT",
        "path": "private/original/location.docx",
    }
    return snapshot, scan, images


def valid_result():
    return {
        "edits": [{"id": "e001", "block_id": "word/document.xml:p000001", "operation": "replace",
                   "old_text": "cat", "new_text": "dog", "context_before": "The ",
                   "context_after": " was", "page": 1, "reason": "Crossed out cat; dog above it",
                   "needs_review": False}],
        "warnings": [],
    }


def envelope(api, result=None):
    text = json.dumps(valid_result() if result is None else result)
    if api == "responses":
        return {"id": "response-example", "status": "completed", "usage": {"input_tokens": 100},
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": text}]}]}
    return {"id": "chat-example", "usage": {"prompt_tokens": 100},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
def test_request_contains_original_scan_and_strict_schema_but_no_reference(inputs, api):
    snapshot, scan, images = inputs
    request = vision.build_request(snapshot, scan, model="vision-test", api=api, page_images=images)
    encoded = json.dumps(request)
    assert "The cat was here." in encoded
    assert snapshot["blocks"][0]["id"] in encoded
    assert snapshot["warnings"][0] in encoded
    assert snapshot["reference"] not in encoded
    assert snapshot["path"] not in encoded
    assert request["model"] == "vision-test"
    if api == "responses":
        assert request["instructions"] == vision.SYSTEM_PROMPT
        assert request["store"] is False
        content = request["input"][0]["content"]
        attachment = next(item for item in content if item["type"] == "input_file")
        assert attachment["filename"] == scan.name
        prefix, data = attachment["file_data"].split(",", 1)
        assert prefix == "data:application/pdf;base64"
        assert base64.b64decode(data) == scan.read_bytes()
        specification = request["text"]["format"]
    else:
        assert request["messages"][0] == {"role": "system", "content": vision.SYSTEM_PROMPT}
        content = request["messages"][1]["content"]
        attachments = [item["image_url"] for item in content if item["type"] == "image_url"]
        assert len(attachments) == len(images)
        for attachment, path in zip(attachments, images):
            assert attachment["detail"] == "high"
            prefix, data = attachment["url"].split(",", 1)
            assert prefix == "data:image/png;base64"
            assert base64.b64decode(data) == path.read_bytes()
        assert [item["text"] for item in content[1:] if item["type"] == "text"] == ["Scan page 1", "Scan page 2"]
        specification = request["response_format"]["json_schema"]
    assert specification["strict"] is True
    assert specification["schema"] == RESPONSE_SCHEMA


def test_chat_mode_cannot_silently_send_no_scan_pages(inputs):
    snapshot, scan, _ = inputs
    with pytest.raises(ValueError, match="rendered scan pages"):
        vision.build_request(snapshot, scan, model="vision-test", api="chat-completions", page_images=[])


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
def test_complete_response_returns_validated_edits(api):
    assert vision.parse_response(envelope(api), api) == valid_result()


@pytest.mark.parametrize("status", [None, "incomplete", "failed", "cancelled", "in_progress"])
def test_responses_rejects_incomplete_even_when_json_looks_valid(status):
    response = envelope("responses")
    response["status"] = status
    with pytest.raises(vision.ProviderError):
        vision.parse_response(response, "responses")


@pytest.mark.parametrize("finish", [None, "length", "content_filter", "tool_calls"])
def test_chat_rejects_incomplete_even_when_json_looks_valid(finish):
    response = envelope("chat-completions")
    response["choices"][0]["finish_reason"] = finish
    with pytest.raises(vision.ProviderError):
        vision.parse_response(response, "chat-completions")


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
def test_refusal_is_not_accepted_as_empty_edits(api):
    response = envelope(api)
    if api == "responses":
        response["output"][0]["content"].append({"type": "refusal", "refusal": "Cannot interpret"})
    else:
        response["choices"][0]["message"]["refusal"] = "Cannot interpret"
    with pytest.raises(vision.ProviderError, match="refused"):
        vision.parse_response(response, api)


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
@pytest.mark.parametrize("case", ["extra_root", "extra_edit", "missing_edit", "page_boolean", "review_string", "duplicate_id", "unknown_operation", "unlocated"])
def test_model_data_must_pass_local_validation_in_both_modes(api, case):
    result = valid_result()
    edit = result["edits"][0]
    if case == "extra_root":
        result["instructions"] = "Do something else"
    elif case == "extra_edit":
        edit["execute"] = "unrecognized property"
    elif case == "missing_edit":
        del edit["old_text"]
    elif case == "page_boolean":
        edit["page"] = True
    elif case == "review_string":
        edit["needs_review"] = "false"
    elif case == "duplicate_id":
        result["edits"].append(copy.deepcopy(edit))
    elif case == "unknown_operation":
        edit["operation"] = "rewrite-document"
    else:
        edit["block_id"] = ""
    with pytest.raises(vision.ProviderError, match="invalid edit list"):
        vision.parse_response(envelope(api, result), api)


def test_unlocated_mark_can_be_retained_for_review_and_page_must_exist():
    result = valid_result()
    result["edits"][0].update(block_id="", needs_review=True, page=3)
    assert validate_response(result, page_count=3) == result
    with pytest.raises(ValueError, match="page"):
        validate_response(result, page_count=2)


@pytest.mark.parametrize("api,response", [
    ("responses", {"status": "completed", "output": None}),
    ("responses", {"status": "completed", "output": [None]}),
    ("responses", {"status": "completed", "output": [{"type": "message", "content": [None]}]}),
    ("responses", {"status": "completed", "output": [{"type": "message", "content": None}]}),
    ("chat-completions", {"choices": None}),
    ("chat-completions", {"choices": [None]}),
    ("chat-completions", {"choices": [{"finish_reason": "stop", "message": None}]}),
])
def test_malformed_provider_envelopes_fail_with_a_handled_provider_error(api, response):
    with pytest.raises(vision.ProviderError):
        vision.parse_response(response, api)


@pytest.mark.parametrize("base", [
    "http://provider.example/v1", "http://127.0.0.1.example/v1", "ftp://provider.example/v1",
    "https://name:secret@provider.example/v1", "https://provider.example/v1?api_key=secret",
    "https://provider.example/v1#fragment",
])
def test_endpoint_rejects_remote_http_or_embedded_credentials(base):
    with pytest.raises(ValueError):
        vision.endpoint_url(base, "responses")


@pytest.mark.parametrize("base", ["http://localhost:8000/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"])
def test_local_http_is_supported_for_local_model(base):
    assert vision.endpoint_url(base, "chat-completions") == base + "/chat/completions"


def fake_transport(monkeypatch, response):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            handle = io.BytesIO(json.dumps(response).encode())
            handle.headers = {"x-request-id": "request-example"}
            return handle

    def create(*handlers):
        assert vision._NoRedirect in handlers
        return Opener()

    monkeypatch.setattr(vision.urllib.request, "build_opener", create)
    return requests


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
def test_propose_http_contract_and_provenance_offline(monkeypatch, inputs, api):
    snapshot, scan, images = inputs
    before = scan.read_bytes()
    requests = fake_transport(monkeypatch, envelope(api))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    plan = vision.propose(snapshot, scan, model="vision-test", api=api, pdf_info={"pages": 2}, page_images=images, timeout=15)
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.get_method() == "POST"
    assert timeout == 15
    assert request.get_header("Authorization") == "Bearer test-only-secret"
    assert "test-only-secret" not in request.data.decode()
    assert "test-only-secret" not in json.dumps(plan)
    assert plan["source_sha256"] == snapshot["sha256"]
    assert plan["metadata"]["mode"] == "live_model"
    assert plan["metadata"]["request_id"] == "request-example"
    assert plan["metadata"]["scan_pages"] == 2
    assert plan["edits"] == valid_result()["edits"]
    assert scan.read_bytes() == before


def test_remote_requires_key_before_any_connection(inputs):
    snapshot, scan, _ = inputs
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        vision.propose(snapshot, scan, model="vision-test", pdf_info={"pages": 1})


def test_remote_http_rejected_before_sending_credentials(monkeypatch, inputs):
    snapshot, scan, _ = inputs
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    with pytest.raises(ValueError, match="HTTPS"):
        vision.propose(snapshot, scan, model="vision-test", pdf_info={"pages": 1}, base_url="http://provider.example/v1")


def test_local_model_without_api_key_sends_no_authorization(monkeypatch, inputs):
    snapshot, scan, images = inputs
    requests = fake_transport(monkeypatch, envelope("chat-completions"))
    vision.propose(snapshot, scan, model="local-vision", pdf_info={"pages": 2}, api="chat-completions",
                   base_url="http://localhost:8000/v1", page_images=images)
    assert requests[0][0].get_header("Authorization") is None


def test_model_cannot_cite_nonexistent_scan_page(monkeypatch, inputs):
    snapshot, scan, _ = inputs
    result = valid_result()
    result["edits"][0]["page"] = 2
    fake_transport(monkeypatch, envelope("responses", result))
    with pytest.raises(ValueError, match="page"):
        vision.propose(snapshot, scan, model="local-vision", pdf_info={"pages": 1}, base_url="http://localhost:8000/v1")


def test_redirect_never_resends_authorization():
    with pytest.raises(vision.ProviderError, match="redirected"):
        vision._NoRedirect().redirect_request(None, None, 307, "redirect", {}, "http://other.example/v1")

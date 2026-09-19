"""Provider error bodies must reduce to the sentence a human needs.

These fixtures are real bodies captured from this router's own providers
while diagnosing a config where most models had quietly died.
"""
import json

import pytest

from flexrouter.errors import describe_http_error, extract_error_message


def test_cerebras_archived_model():
    body = ('{"message":"Model zai-glm-4.7 is archived and unavailable for the '
            'organization.","type":"model_archived_error","param":"model",'
            '"code":"model_archived"}')
    assert extract_error_message(body) == (
        "Model zai-glm-4.7 is archived and unavailable for the organization.")


def test_googleai_wraps_its_error_in_a_list():
    # Google returns a JSON *array*, which a naive ["error"]["message"] lookup
    # misses entirely.
    body = ('[{\n  "error": {\n    "code": 404,\n    "message": "This model '
            'models/gemini-2.0-flash is no longer available. Please update '
            'your code to use models/gemini-3.6-flash.",\n    "status": '
            '"NOT_FOUND"\n  }\n}]')
    message = extract_error_message(body)
    assert message.startswith("This model models/gemini-2.0-flash is no longer available")
    assert "gemini-3.6-flash" in message


def test_openai_style_nested_error():
    body = '{"error":{"message":"Incorrect API key provided","type":"invalid_request_error"}}'
    assert extract_error_message(body) == "Incorrect API key provided"


def test_cerebras_payment_required():
    body = ('{"message":"Payment required to access this resource. Visit your '
            'billing tab.","type":"payment_required_error"}')
    assert "Visit your billing tab" in extract_error_message(body)


def test_plain_string_error_field():
    assert extract_error_message('{"error":"slow down"}') == "slow down"


def test_non_json_body_is_still_useful():
    assert extract_error_message("<html>502 Bad Gateway</html>") == "<html>502 Bad Gateway</html>"


def test_whitespace_is_collapsed():
    # Built with json.dumps so the escaping is unambiguous - providers do wrap
    # messages across lines.
    body = json.dumps({"message": "line one" + chr(10) + chr(10) + "   line two"})
    assert extract_error_message(body) == "line one line two"


@pytest.mark.parametrize("body", [None, "", "   ", "{}", "[]", '{"unrelated": 5}'])
def test_nothing_to_say_returns_none(body):
    assert extract_error_message(body) is None


def test_long_messages_are_clipped():
    body = '{"message":"' + "x" * 500 + '"}'
    out = extract_error_message(body)
    assert len(out) <= 300
    assert out.endswith("…")


def test_describe_includes_status_and_route():
    line = describe_http_error(404, "googleai", "models/gemini-2.0-flash",
                               '{"error":{"message":"no longer available"}}')
    assert line == "404 from googleai/models/gemini-2.0-flash: no longer available"


def test_describe_without_a_message_still_names_the_route():
    assert describe_http_error(500, "groq", "llama", "") == "500 from groq/llama"


def test_describe_accepts_bytes():
    line = describe_http_error(402, "cerebras", None, b'{"message":"Payment required"}')
    assert line == "402 from cerebras: Payment required"

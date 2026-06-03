import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.genai import types  # noqa: E402

from generators.models.gemini import GeminiGenerator, MAX_TOOL_ITERATIONS  # noqa: E402
from generators.models.tools import TOOL_REGISTRY  # noqa: E402


def _contents_at(client, call_index):
    """Returns the `contents` kwarg passed to the Nth generate_content call."""
    return client.models.generate_content.call_args_list[call_index].kwargs["contents"]


@pytest.fixture(autouse=True)
def _stub_genai_client():
    """Replace genai.Client so no real Vertex traffic happens."""
    with patch("generators.models.gemini.genai.Client") as mock_client_cls:
        instance = MagicMock()
        mock_client_cls.return_value = instance
        yield instance


def _config(tools=None):
    cfg = {
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
        "vertex_model": "gemini-2.5-pro",
        "base_prompt": "",
    }
    if tools is not None:
        cfg["tools"] = tools
    return cfg


def _text_response(text):
    """Use real types.Content so test assertions can walk the response shape
    the same way the production code does. The fields we don't care about
    (e.g. usage metadata) stay as MagicMocks via __getattr__."""
    resp = MagicMock()
    resp.text = text
    resp.candidates = [
        MagicMock(content=types.Content(role="model", parts=[types.Part(text=text)]))
    ]
    return resp


def _function_call_response(name, args):
    resp = MagicMock()
    resp.text = ""
    resp.candidates = [
        MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part(functionCall=types.FunctionCall(name=name, args=args))],
            )
        )
    ]
    return resp


def _function_response_parts(contents):
    """Walks a real list of types.Content and returns the FunctionResponse
    objects. Skips non-Content entries (e.g. MagicMock pass-through from a
    mocked response's .candidates[0].content)."""
    out = []
    for content in contents:
        if not isinstance(content, types.Content):
            continue
        for part in content.parts or []:
            if part.function_response is not None:
                out.append(part.function_response)
    return out


def test_no_tools_uses_single_shot_path(_stub_genai_client):
    """Config without `tools:` must hit the original single-shot path
    with no `config=` kwarg — preserving today's behavior bit-for-bit
    for every existing judge config."""
    response = MagicMock(spec_set=["text"])
    response.text = "PASS\nAll good."
    _stub_genai_client.models.generate_content.return_value = response

    # The single-shot path uses isinstance(response, GenerateContentResponse)
    # to gate the assignment to `r`. Spoof that by patching the class in
    # the gemini module so MagicMock passes the isinstance check.
    with patch("generators.models.gemini.GenerateContentResponse", MagicMock):
        gen = GeminiGenerator(_config())
        result = gen.generate_internal("hello")

    assert result == "PASS\nAll good."
    call_args = _stub_genai_client.models.generate_content.call_args
    assert call_args.kwargs["model"] == "gemini-2.5-pro"
    assert call_args.kwargs["contents"] == "hello"
    assert "config" not in call_args.kwargs


def test_unknown_tool_name_fails_fast(_stub_genai_client):
    with pytest.raises(ValueError, match="Unknown tools"):
        GeminiGenerator(_config(tools=["nope_not_a_tool"]))


def test_tools_configured_but_no_function_call_returns_text(_stub_genai_client):
    """With tools enabled but the model emits text on the first turn, the
    loop exits immediately with that text. No tool is invoked."""
    _stub_genai_client.models.generate_content.return_value = _text_response(
        "PASS\nBeam is on 2.99.0."
    )

    gen = GeminiGenerator(_config(tools=["fetch_url"]))
    result = gen.generate_internal("rate this conversation")

    assert "PASS" in result
    assert _stub_genai_client.models.generate_content.call_count == 1
    call_args = _stub_genai_client.models.generate_content.call_args
    assert call_args.kwargs["config"] is not None


def test_tool_call_loop_invokes_tool_and_returns_final_text(_stub_genai_client):
    """Model asks for fetch_url, judge runs it, model gets the result and
    returns a final text answer. Verifies the two-turn loop and that the
    second SDK call carries a FunctionResponse part."""
    _stub_genai_client.models.generate_content.side_effect = [
        _function_call_response("fetch_url", {"url": "https://beam.apache.org/x"}),
        _text_response("PASS\nVersion 2.99.0 was fetched."),
    ]

    fake_tool_result = "Apache Beam latest: 2.99.0"
    with patch.object(TOOL_REGISTRY["fetch_url"], "fn", return_value=fake_tool_result) as mock_fn:
        gen = GeminiGenerator(_config(tools=["fetch_url"]))
        result = gen.generate_internal("what version did the agent name?")

    assert "PASS" in result
    mock_fn.assert_called_once_with({"url": "https://beam.apache.org/x"})

    assert _stub_genai_client.models.generate_content.call_count == 2
    function_responses = _function_response_parts(
        _contents_at(_stub_genai_client, 1)
    )
    assert function_responses, "second SDK call must carry the FunctionResponse part"
    assert function_responses[0].name == "fetch_url"
    assert function_responses[0].response == {"result": fake_tool_result}


def test_tool_loop_terminates_at_iteration_cap(_stub_genai_client):
    """Pathological model that always emits a function_call must not hang;
    the loop must terminate at MAX_TOOL_ITERATIONS."""
    _stub_genai_client.models.generate_content.side_effect = [
        _function_call_response("fetch_url", {"url": f"https://example.com/{i}"})
        for i in range(MAX_TOOL_ITERATIONS + 5)
    ]

    with patch.object(TOOL_REGISTRY["fetch_url"], "fn", return_value="ok"):
        gen = GeminiGenerator(_config(tools=["fetch_url"]))
        result = gen.generate_internal("loop me")

    assert _stub_genai_client.models.generate_content.call_count == MAX_TOOL_ITERATIONS
    assert isinstance(result, str)


def test_tool_raising_is_surfaced_as_error_string(_stub_genai_client):
    """A tool that raises must not crash the judge; the exception is
    converted to an `Error: ...` string and fed back to the model."""
    _stub_genai_client.models.generate_content.side_effect = [
        _function_call_response("fetch_url", {"url": "https://example.com/"}),
        _text_response("FAIL\nCould not verify."),
    ]

    def boom(_):
        raise RuntimeError("network down")

    with patch.object(TOOL_REGISTRY["fetch_url"], "fn", side_effect=boom):
        gen = GeminiGenerator(_config(tools=["fetch_url"]))
        result = gen.generate_internal("verify")

    assert "FAIL" in result
    function_responses = _function_response_parts(
        _contents_at(_stub_genai_client, 1)
    )
    assert function_responses, "second SDK call must carry the FunctionResponse part"
    payload = function_responses[0].response["result"]
    assert "Error: RuntimeError" in payload
    assert "network down" in payload

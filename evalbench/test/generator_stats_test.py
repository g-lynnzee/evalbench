import json
from generators.models.gemini_cli import GeminiCliGenerator
from generators.models.claude_code import ClaudeCodeGenerator
from generators.models.codex_cli import CodexCliGenerator


def test_gemini_cli_stats_unresolved_tool_call():
    # Mock stream output where:
    # call-1 is successful
    # call-2 is an error
    # call-3 is unresolved (stalled)
    mock_stream = "\n".join([
        '{"type": "init", "session_id": "session-123", '
        '"model": "gemini-2.5-flash"}',
        '{"type": "tool_use", "tool_id": "call-1", '
        '"tool_name": "list_instances", "parameters": {}}',
        '{"type": "tool_result", "tool_id": "call-1", '
        '"status": "success", "result": []}',
        '{"type": "tool_use", "tool_id": "call-2", '
        '"tool_name": "list_instances", "parameters": {}}',
        '{"type": "tool_result", "tool_id": "call-2", '
        '"status": "error", "result": "error description"}',
        '{"type": "tool_use", "tool_id": "call-3", '
        '"tool_name": "list_instances", "parameters": {}}',
        '{"type": "result", "stats": {"duration_ms": 100, '
        '"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}}'
    ])

    generator = GeminiCliGenerator({})
    parsed_str = generator._parse_stream_json(mock_stream)
    parsed = json.loads(parsed_str)

    stats = parsed["stats"]["tools"]
    assert stats["totalCalls"] == 3
    assert stats["totalSuccess"] == 1
    # call-2 (status="error") and call-3 (status=None) should count as failures
    assert stats["totalFail"] == 2


def test_claude_code_stats_unresolved_tool_call():
    # Mock stream output where:
    # call-1 is successful
    # call-2 is an error
    # call-3 is unresolved (stalled)
    mock_stream = "\n".join([
        '{"type": "system", "session_id": "session-123", '
        '"model": "claude-3-5-sonnet"}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", '
        '"id": "call-1", "name": "mcp__server__list", "input": {}}]}}',
        '{"type": "tool_result", "tool_use_id": "call-1", '
        '"is_error": false, "content": []}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", '
        '"id": "call-2", "name": "mcp__server__list", "input": {}}]}}',
        '{"type": "tool_result", "tool_use_id": "call-2", '
        '"is_error": true, "content": "error details"}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", '
        '"id": "call-3", "name": "mcp__server__list", "input": {}}]}}',
        '{"type": "result", "session_id": "session-123", '
        '"usage": {"input_tokens": 10, "output_tokens": 10}}'
    ])

    generator = ClaudeCodeGenerator({})
    parsed_str = generator._parse_stream_json(mock_stream)
    parsed = json.loads(parsed_str)

    stats = parsed["stats"]["tools"]
    assert stats["totalCalls"] == 3
    assert stats["totalSuccess"] == 1
    # call-2 (status="error") and call-3 (status=None) should count as failures
    assert stats["totalFail"] == 2


def test_codex_cli_stats_unresolved_tool_call():
    # Mock stream output where:
    # call-1 is successful
    # call-2 is an error
    # call-3 is unresolved (stalled)
    # We place 'type' inside 'details' to conform to Codex CLI's
    # payload structure:
    mock_stream = "\n".join([
        '{"type": "thread.started", "thread_id": "session-123"}',
        '{"type": "item.started", "item": {"id": "call-1", '
        '"details": {"type": "mcp_tool_call", "server": "s", '
        '"tool": "list"}}}',
        '{"type": "item.completed", "item": {"id": "call-1", '
        '"details": {"type": "mcp_tool_call", "server": "s", '
        '"tool": "list", "status": "success", "result": "ok"}}}',
        '{"type": "item.started", "item": {"id": "call-2", '
        '"details": {"type": "mcp_tool_call", "server": "s", '
        '"tool": "list"}}}',
        '{"type": "item.completed", "item": {"id": "call-2", '
        '"details": {"type": "mcp_tool_call", "server": "s", '
        '"tool": "list", "error": "error"}}}',
        '{"type": "item.started", "item": {"id": "call-3", '
        '"details": {"type": "mcp_tool_call", "server": "s", '
        '"tool": "list"}}}',
        '{"type": "turn.completed", "usage": {"input_tokens": 10, '
        '"output_tokens": 10}}'
    ])

    generator = CodexCliGenerator({})
    parsed_str = generator._parse_stream_json(mock_stream)
    parsed = json.loads(parsed_str)

    stats = parsed["stats"]["tools"]
    assert stats["totalCalls"] == 3
    assert stats["totalSuccess"] == 1
    # call-2 (status="error") and call-3 (status=None) should count as failures
    assert stats["totalFail"] == 2

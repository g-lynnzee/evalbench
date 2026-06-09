import json
from generators.models.gemini_cli import GeminiCliGenerator
from scorers.util import format_conversation_history

# Mock stream JSON output from Gemini CLI representing a turn
mock_stream_output = """
{"type": "init", "session_id": "session-123", "model": "gemini-2.5-flash"}
{"type": "message", "role": "assistant", "content": "I will check the instances."}
{"type": "tool_use", "tool_id": "call-abc", "tool_name": "cloud-sql__list_instances", "parameters": {"project": "my-project"}, "timestamp": "2026-06-09T15:00:00Z"}
{"type": "tool_result", "tool_id": "call-abc", "status": "success", "result": [{"name": "instance-primary", "state": "RUNNABLE"}], "timestamp": "2026-06-09T15:00:02Z"}
{"type": "message", "role": "assistant", "content": " I found one Cloud SQL instance named instance-primary."}
{"type": "result", "stats": {"duration_ms": 2500, "input_tokens": 150, "output_tokens": 80, "total_tokens": 230}}
"""

print("--- 1. Parsing Generator Stream ---")
generator = GeminiCliGenerator({})
parsed_str = generator._parse_stream_json(mock_stream_output)
parsed_json = json.loads(parsed_str)

print(json.dumps(parsed_json, indent=2))

print("\n--- 2. Formatting History for LLM Scorers (include_tool_calls=True) ---")
history = [
    {
        "user": "List my Cloud SQL instances.",
        "agent": parsed_str
    }
]

formatted_transcript = format_conversation_history(history, include_tool_calls=True)
print(formatted_transcript)

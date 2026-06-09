import json
from scorers.util import format_conversation_history

def test_format_conversation_history_without_tools():
    history = [
        {"user": "List cloud SQL instances.", "agent": json.dumps({"response": "Sure, looking.", "tool_calls": []})},
        {"user": "Thanks.", "agent": "No problem."}
    ]
    formatted = format_conversation_history(history, include_tool_calls=False)
    
    expected = (
        "User: List cloud SQL instances.\n"
        "Agent: Sure, looking.\n"
        "User: Thanks.\n"
        "Agent: No problem.\n"
    )
    assert formatted == expected

def test_format_conversation_history_with_tools():
    history = [
        {
            "user": "List cloud SQL instances.",
            "agent": json.dumps({
                "response": "Found instance-1.",
                "tool_calls": [
                    {
                        "tool_name": "cloud-sql__list_instances",
                        "parameters": {"project": "my-project"},
                        "status": "success",
                        "response": [{"name": "instance-1", "state": "RUNNABLE"}]
                    }
                ]
            })
        }
    ]
    formatted = format_conversation_history(history, include_tool_calls=True)
    
    expected_part_1 = "User: List cloud SQL instances.\n"
    expected_part_2 = "Agent invoked cloud-sql__list_instances(project=\"my-project\") -> Success:\n"
    expected_part_3 = (
        "  [\n"
        "    {\n"
        "      \"name\": \"instance-1\",\n"
        "      \"state\": \"RUNNABLE\"\n"
        "    }\n"
        "  ]\n"
    )
    expected_part_4 = "Agent: Found instance-1.\n"
    
    assert expected_part_1 in formatted
    assert expected_part_2 in formatted
    assert expected_part_3 in formatted
    assert expected_part_4 in formatted

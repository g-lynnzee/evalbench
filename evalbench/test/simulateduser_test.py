import json
from generators.prompts.simulateduser import SimulatedUserPromptGenerator


def test_simulated_user_prompt_generator():
    generator = SimulatedUserPromptGenerator(None, {})

    # Test case where agent output is JSON
    history = [
        {"user": "hello", "agent": json.dumps({"response": "hi there", "stats": {}, "tool_calls": []})},
    ]
    last_reply = json.dumps({"response": "how can I help?", "stats": {}, "tool_calls": []})

    item = {
        "conversation_plan": "Be polite",
        "history": history,
        "last_agent_reply": last_reply
    }

    res = generator.generate(item)
    prompt = res["prompt"]

    assert "User: hello\nAgent: hi there" in prompt
    assert "how can I help?" in prompt

    # Test case where agent output is NOT JSON
    history_plain = [
        {"user": "hello", "agent": "hi plain"},
    ]
    last_reply_plain = "plain help"

    item_plain = {
        "conversation_plan": "Be polite",
        "history": history_plain,
        "last_agent_reply": last_reply_plain
    }

    res_plain = generator.generate(item_plain)
    prompt_plain = res_plain["prompt"]

    assert "User: hello\nAgent: hi plain" in prompt_plain
    assert "plain help" in prompt_plain

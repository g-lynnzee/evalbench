"""Utility functions to aid the scorers."""

from typing import Any
import logging
import hashlib
import pickle


def with_cache_execute(
    prompt: str,
    model: str,
    execution_method: Any,
    cache_client: Any,
) -> Any:
    """
    Execute a task with caching support. If the result exists in the cache,
    it retrieves it; otherwise, it executes the method and caches the result.

    Args:
        prompt (str): The input prompt for the execution.
        model (str): The model identifier.
        execution_method (Callable[[str], Any]): The method to execute if the result is not cached.
        cache_client (Any): The caching client (e.g., Redis).

    Returns:
        Any: The execution result.
    """
    # Generate a hash of the prompt and model
    query_hash = hashlib.sha256((prompt + model).encode()).hexdigest()

    # Attempt to retrieve from cache
    try:
        cached_result = cache_client.get(query_hash)
        if cached_result is not None:  # Ensure the result is valid
            logging.debug("Found cached result for comparing prompt")
            return pickle.loads(cached_result)
    except Exception as e:
        logging.warning(f"Failed to retrieve query from cache: {e}")

    # Execute the method as the result is not cached
    try:
        response = execution_method(prompt)
    except Exception as e:
        logging.error(
            f"Execution method failed for prompt: {prompt} with error: {e}")
        return None

    # Attempt to cache the result
    try:
        cache_client.set(query_hash, pickle.dumps(response))
        logging.debug(f"Cached result for prompt")
    except Exception as e:
        logging.warning(f"Failed to cache query result: {e}")

    return response


def make_hashable(value):
    if isinstance(value, list):
        return tuple(make_hashable(v) for v in value)
    elif isinstance(value, dict):
        return frozenset((k, make_hashable(v)) for k, v in value.items())
    return value


def format_conversation_history(
    history: list[dict[str, str]], include_tool_calls: bool = False
) -> str:
    import json

    transcript = ""
    for turn in history:
        user_msg = turn.get("user", "")
        agent_raw = turn.get("agent", "")

        transcript += f"User: {user_msg}\n"

        agent_text = agent_raw
        tool_calls_text = ""

        try:
            parsed = json.loads(agent_raw)
            if isinstance(parsed, dict):
                agent_text = parsed.get("response", agent_raw)
                if include_tool_calls and "tool_calls" in parsed:
                    calls = parsed["tool_calls"]
                    if isinstance(calls, list):
                        for call in calls:
                            tname = call.get("tool_name", "unknown")
                            params = call.get("parameters", {})
                            status = call.get("status")
                            resp = call.get("response")

                            if isinstance(params, dict):
                                params_str = ", ".join(f"{k}={json.dumps(v)}" for k, v in params.items())
                            else:
                                params_str = str(params)

                            status_str = str(status) if status is not None else "Unknown"
                            status_str = status_str[0].upper() + status_str[1:] if status_str else "Unknown"

                            if resp is not None:
                                if isinstance(resp, (dict, list)):
                                    resp_str = json.dumps(resp, indent=2)
                                else:
                                    resp_str = str(resp)
                                resp_lines = resp_str.split("\n")
                                resp_str = "\n".join("  " + line for line in resp_lines)
                                tool_calls_text += f"Agent invoked {tname}({params_str}) -> {status_str}:\n{resp_str}\n"
                            else:
                                tool_calls_text += f"Agent invoked {tname}({params_str}) -> {status_str}\n"
        except (json.JSONDecodeError, TypeError):
            # agent_raw is plain text, not JSON; keep original value
            pass

        if tool_calls_text:
            transcript += tool_calls_text
        transcript += f"Agent: {agent_text}\n"

    return transcript

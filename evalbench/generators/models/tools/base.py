from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass
class Tool:
    """A function-callable tool the judge model can invoke.

    `input_schema` is plain JSON Schema (object with `type`, `properties`,
    `required`). Each judge backend translates it into its native shape.

    `fn` receives the model-supplied args as a dict and returns a string
    payload. Errors should be returned as `"Error: ..."` strings rather
    than raised so the model can observe the failure and react.
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    fn: Callable[[Dict[str, Any]], str]

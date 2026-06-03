import logging
import os
import time

from google import genai
from google.api_core.exceptions import ResourceExhausted
from google.genai import types
from google.genai.types import GenerateContentResponse

from .generator import QueryGenerator
from .tools import get_tools
from util.gcp import get_gcp_project, get_gcp_region
from util.rate_limit import ResourceExhaustedError
from util.sanitizer import sanitize_sql


MAX_TOOL_ITERATIONS = 5


class GeminiGenerator(QueryGenerator):
    """Generator queries using Vertex model."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gcp_vertex_gemini"
        self.project_id = get_gcp_project(
            querygenerator_config.get("gcp_project_id"))
        self.region = get_gcp_region(querygenerator_config.get("gcp_region"))
        if not self.project_id or not self.region:
            # Attempt to use GEMINI_API_KEY for authentication
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "Both gcp_project_id and gcp_region must be set in "
                    "config when GEMINI_API_KEY is not available."
                )
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client(
                vertexai=True, project=self.project_id, location=self.region
            )

        self.vertex_model = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config.get("base_prompt") or ""
        self.generation_config = None
        self.base_prompt = self.base_prompt

        # Opt-in tool use. Unknown names fail fast at construction.
        tool_names = querygenerator_config.get("tools") or []
        self.tools = get_tools(tool_names) if tool_names else []
        self._tools_by_name = {t.name: t for t in self.tools}
        self._genai_tool_config = self._build_genai_tools() if self.tools else None

    def _build_genai_tools(self):
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parametersJsonSchema=t.input_schema,
            )
            for t in self.tools
        ]
        return [types.Tool(functionDeclarations=declarations)]

    def generate_internal(self, prompt):
        if self._genai_tool_config:
            return self._generate_with_tools(prompt)
        return self._generate_single_shot(prompt)

    def _generate_single_shot(self, prompt):
        logger = logging.getLogger(__name__)
        try:
            response = self._call_generate_content(
                contents=self.base_prompt + prompt,
            )
        except Exception:
            logger.exception("Unhandled exception during generate_content")
            raise
        if isinstance(response, GenerateContentResponse):
            return sanitize_sql(response.text)
        # Preserved from the original implementation, which referenced an
        # unbound `r` on this branch and would crash with UnboundLocalError.
        # Returning the raw response is strictly an improvement on that.
        return response

    def _generate_with_tools(self, prompt):
        """Tool-call loop bounded by MAX_TOOL_ITERATIONS."""
        logger = logging.getLogger(__name__)
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=self.base_prompt + prompt)],
            )
        ]
        config = types.GenerateContentConfig(tools=self._genai_tool_config)

        last_text = ""
        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self._call_generate_content(
                contents=contents, config=config,
            )
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                last_text = response.text or ""
                return sanitize_sql(last_text)

            contents.append(response.candidates[0].content)
            tool_response_parts = []
            for fc in function_calls:
                tool = self._tools_by_name.get(fc.name)
                if tool is None:
                    result = f"Error: unknown tool '{fc.name}'"
                else:
                    try:
                        result = tool.fn(dict(fc.args or {}))
                    except Exception as e:
                        logger.exception("Tool '%s' raised", fc.name)
                        result = f"Error: {type(e).__name__}: {e}"
                tool_response_parts.append(
                    types.Part(
                        functionResponse=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=tool_response_parts))

        logger.warning(
            "Gemini judge exhausted %d tool iterations; returning last text.",
            MAX_TOOL_ITERATIONS,
        )
        return sanitize_sql(last_text)

    def _extract_function_calls(self, response):
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        parts = getattr(candidates[0].content, "parts", None) or []
        return [p.function_call for p in parts if getattr(p, "function_call", None)]

    def _call_generate_content(self, **kwargs):
        """Wraps models.generate_content with retry/backoff on rate limits."""
        logger = logging.getLogger(__name__)
        for attempt in range(5):
            try:
                return self.client.models.generate_content(
                    model=self.vertex_model, **kwargs,
                )
            except ResourceExhausted as e:
                if attempt >= 4:
                    raise ResourceExhaustedError(e)
                logger.warning(
                    "ResourceExhausted. Retrying attempt %d after sleep...",
                    attempt + 1,
                )
                time.sleep(2 ** attempt * 2)
            except genai.errors.ClientError as e:
                msg = str(e)
                if "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
                    raise
                if attempt >= 4:
                    raise ResourceExhaustedError(e)
                logger.warning(
                    "429/RESOURCE_EXHAUSTED. Retrying attempt %d "
                    "after sleep...",
                    attempt + 1,
                )
                time.sleep(2 ** attempt * 2)

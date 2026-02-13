"""ADKScorer implementation."""

from typing import Tuple, Any
from scorers import comparator


class ADKScorer(comparator.Comparator):
    """
    ADKScorer class implements the Comparator base class using ADK evaluation.

    Attributes:
      1. name: Name of the comparator. Set to "adk_scorer"
      2. config: the scorer config defined in the run config yaml file
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "adk_scorer"
        self.config = config

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:
        """compare function implements the comparison logic for ADKScorer.

        For now, this is a placeholder implementation.
        """
        # Placeholder logic: return 100.0 if there are no errors, else 0.0
        if generated_error:
            return 0.0, f"Generated query had an error: {generated_error}"

        return 100.0, "ADK scoring completed successfully (placeholder)."

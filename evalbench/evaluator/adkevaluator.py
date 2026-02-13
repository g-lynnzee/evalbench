from typing import Any, List
import datetime
from dataset.evaladkinput import EvalADKRequest
from dataset.evaladkoutput import EvalADKOutput
from queue import Queue
from databases import DB
import logging
import asyncio
from rich.console import Console
from google.genai.types import Part
from google.adk.evaluation.agent_evaluator import (
    AgentEvaluator,
    EvalConfig,
    EvalSet,
    get_eval_metrics_from_config,
    UserSimulatorProvider,
    EvalStatus,
)
from google.adk.evaluation.eval_result import EvalCaseResult
from dotenv import load_dotenv
from typing import Optional

console = Console()


class ADKEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        self.agent_name = config["agent_name"]
        self.agent_module = config["agent_module"]
        self.eval_dataset_file = config["dataset_config"]
        if "initial_session_file" in config:
            self.initial_session_file = config["initial_session_file"]
        else:
            self.initial_session_file = None

    def evaluate(
        self,
        dataset: List[EvalADKRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running ADK evaluation")
        for eval_input in dataset:
            self._evaluate(eval_input, eval_outputs, job_id, run_time, scoring_results)
        return eval_outputs, scoring_results

    def _evaluate(
        self,
        eval_input: EvalADKRequest,
        eval_outputs: List[Any],
        job_id: str,
        run_time: datetime.datetime,
        scoring_results: List[Any],
        num_runs: int = 1,
    ):
        test_files = [self.eval_dataset_file]

        initial_session = AgentEvaluator._get_initial_session(self.initial_session_file)

        for test_file in test_files:
            eval_output = EvalADKOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            eval_output["test_file"] = test_file
            eval_config = AgentEvaluator.find_config_for_test_file(test_file)
            eval_set = AgentEvaluator._load_eval_set_from_file(
                test_file, eval_config, initial_session
            )
            eval_output["eval_set"] = eval_set
            asyncio.run(
                self.evaluate_eval_set(
                    eval_output,
                    scoring_results,
                    agent_module=self.agent_module,
                    eval_set=eval_set,
                    eval_config=eval_config,
                    num_runs=num_runs,
                    agent_name=self.agent_name,
                )
            )

    async def evaluate_eval_set(
        self,
        eval_output: EvalADKOutput,
        scoring_results: List[Any],
        agent_module: str,
        eval_set: EvalSet,
        criteria: Optional[dict[str, float]] = None,
        eval_config: Optional[EvalConfig] = None,
        num_runs: int = 1,
        agent_name: Optional[str] = None,
    ):
        agent_for_eval = await AgentEvaluator._get_agent_for_eval(
            module_name=agent_module, agent_name=None
        )
        eval_metrics = get_eval_metrics_from_config(eval_config)

        user_simulator_provider = UserSimulatorProvider(
            user_simulator_config=eval_config.user_simulator_config
        )

        eval_results_by_eval_id = await AgentEvaluator._get_eval_results_by_eval_id(
            agent_for_eval=agent_for_eval,
            eval_set=eval_set,
            eval_metrics=eval_metrics,
            num_runs=num_runs,
            user_simulator_provider=user_simulator_provider,
        )
        self.process_results(eval_results_by_eval_id)
        eval_output["eval_results_by_eval_id"] = eval_results_by_eval_id
        return eval_results_by_eval_id

    def merge_parts(self, parts: list[Part]):
        return "".join([part.text for part in parts])

    def process_eval_result(
        self,
        eval_result: EvalCaseResult,
    ):
        for eval_metric_result in eval_result.overall_eval_metric_results:
            console.print(
                f"{eval_metric_result.metric_name}: {eval_metric_result.eval_status}: {eval_metric_result.score}"
            )

        for (
            eval_metric_result_per_invocation
        ) in eval_result.eval_metric_result_per_invocation:
            console.print(
                "*************************************************************************************\n"
            )
            console.print(
                f"USER: {self.merge_parts(eval_metric_result_per_invocation.actual_invocation.user_content.parts)}"
            )
            console.print(
                f"ASSISTANT: {self.merge_parts(eval_metric_result_per_invocation.actual_invocation.final_response.parts)}"
            )
            for (
                eval_metric_result
            ) in eval_metric_result_per_invocation.eval_metric_results:
                console.print(
                    f"{eval_metric_result.metric_name}: {eval_metric_result.eval_status}: {eval_metric_result.score}"
                )
            console.print(
                "*************************************************************************************\n"
            )

    def process_results(
        self,
        eval_results_by_eval_id: dict[str, list],
    ):
        for (
            eval_id,
            eval_results,
        ) in eval_results_by_eval_id.items():
            print(f"Processing eval results for eval id: {eval_id}")
            for eval_result in eval_results:
                print(type(eval_result))
                self.process_eval_result(eval_result)

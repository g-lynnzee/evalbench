from evaluator.orchestrator import Orchestrator
import uuid
import datetime
from dataset.evaladkinput import EvalADKRequest
import logging
from evaluator.adkevaluator import ADKEvaluator


class ADKOrchestrator(Orchestrator):
    def __init__(
        self,
        config,
        db_configs,
        setup_config,
        report_progress=False,
    ):
        self.config = config
        self.db_configs = db_configs
        self.setup_config = setup_config
        self.job_id = f"{uuid.uuid4()}"
        self.run_time = datetime.datetime.now()
        self.total_eval_outputs = []
        self.total_scoring_results = []
        self.reporting_total_evals_done = 0
        self.report_progress = False

    def evaluate(self, dataset: list[EvalADKRequest]):
        logging.info("Starting ADK evaluation")
        evaluator = ADKEvaluator(self.config)
        return evaluator.evaluate(dataset, self.job_id, self.run_time)

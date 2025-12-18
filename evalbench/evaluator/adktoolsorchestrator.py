from evaluator.orchestrator import Orchestrator
import uuid
import datetime
from dataset.evaladkinput import EvalADKRequest


class ADKToolsOrchestrator(Orchestrator):
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
        self.report_progress = False  # TODO: report_progress

    def evaluate(self, dataset: list[EvalADKRequest]):
        pass

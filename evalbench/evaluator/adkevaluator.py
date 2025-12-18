from typing import Any, List
import datetime
from dataset.evaladkinput import EvalADKRequest
from queue import Queue
from databases import DB
import logging


class ADKEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config

    def evaluate(
        self,
        dataset: List[EvalADKRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running ADK evaluation")
        return eval_outputs, scoring_results

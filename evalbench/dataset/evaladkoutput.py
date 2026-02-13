from dataset.evaladkinput import EvalADKRequest


class EvalADKOutput(dict):
    def __init__(
        self,
        evaladkinput: EvalADKRequest,
    ):
        self.update(evaladkinput.__dict__)

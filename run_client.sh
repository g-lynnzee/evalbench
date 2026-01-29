EVAL_CONFIG="datasets/bat/example_run_config.yaml"
export PYTHONPATH=./evalbench:./evalbench/evalproto
python3 evalbench/client/eval_client.py --experiment="evalbench/$EVAL_CONFIG" --endpoint="evaluator-zxwxgw5sma-uc.a.run.app"

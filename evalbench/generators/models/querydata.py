from .generator import QueryGenerator
import logging
import asyncio
import json
import requests
from util.rate_limit import ResourceExhaustedError
from rich.console import Console
from rich.markdown import Markdown
from pprint import pprint
from util.session import EvalAgentEngineSessionMgr


class QueryData(QueryGenerator):
    """A generator that implements QueryGenerator for QueryData."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "querydata"
        self.agent_id = querygenerator_config.get("agent_id")
        self.adkapi_server_url = querygenerator_config.get("adkapi_server_url")
        self.generation_config = None
        self.session_mgr = EvalAgentEngineSessionMgr(querygenerator_config)

    def trace_response(self, result, instance_id):
        with open(f"results/responses_{instance_id}.jsonl", "a") as f:
            # Redirect stdout to the file
            f.write(json.dumps(result, indent=4) + "\n")

    def find_last_item(self, part_type, part_name, result):
        last_item = None
        for item in result:
            for part in item["content"]["parts"]:
                if part_type in part:
                    if part_name is None:
                        last_item = part
                    elif (
                        "name" in part[part_type]
                        and part_name == part[part_type]["name"]
                    ):
                        last_item = part
        return last_item

    def print_response(self, result, instance_id):
        for r in result:
            for part in r["content"]["parts"]:
                if "text" in part:
                    md = Markdown(part["text"])
                    Console().print(md)
                elif "functionCall" in part:
                    md = Markdown(f"Function Call: {part['functionCall']['name']}")
                    Console().print(md)
                    md = Markdown(
                        f"Function Args: {part['functionCall']['args']['prompt']}"
                    )
                    Console().print(md)
                elif "functionResponse" in part:
                    Console().print(
                        f"Function Response: {part['functionResponse']['name']}"
                    )
                    pprint(json.loads(part["functionResponse"]["response"]["result"]))
                else:
                    md = Markdown(part)
                    Console().print(md)

    def generate_internal(self, eval_result):
        """Generates a response for the given prompt."""

        item = eval_result["payload"]
        session_id = item["instance_id"]

        try:
            session = self.session_mgr.create_session("dataagent", "evalbench_user")
            session_id = session.id

            payload = {
                "appName": self.agent_id,
                "userId": "evalbench_user",
                "sessionId": session_id,
                "newMessage": {"role": "user", "parts": [{"text": item["prompt"]}]},
            }

            response = requests.post(self.adkapi_server_url + "/run", json=payload)
            response.raise_for_status()
            self.session_mgr.delete_session("dataagent", "evalbench_user", session_id)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code != 404:
                raise ResourceExhaustedError(e)

        self.trace_response(response.json(), session_id)

        functionCall = self.find_last_item(
            "functionCall", "cloud_gda_query_tool_alloydb", response.json()
        )
        functionResponse = self.find_last_item(
            "functionResponse", "cloud_gda_query_tool_alloydb", response.json()
        )
        functionResponseResult = json.loads(
            functionResponse["functionResponse"]["response"]["result"]
        )
        text = self.find_last_item("text", None, response.json())

        eval_result["agent_response"] = response.json()
        if "generatedQuery" not in functionResponseResult:
            eval_result["generated_sql"] = None
        else:
            eval_result["generated_sql"] = functionResponseResult["generatedQuery"]

        if "disambiguationQuestion" not in functionResponseResult:
            eval_result["disambiguation_question"] = None
        else:
            eval_result["disambiguation_question"] = functionResponseResult[
                "disambiguationQuestion"
            ]

        eval_result["tool_prompt"] = functionCall["functionCall"]["args"]["prompt"]
        eval_result["nl_response"] = text["text"]

        return eval_result

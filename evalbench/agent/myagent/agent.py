from google.adk import Agent
from google.adk.apps import App
from toolbox_core import ToolboxSyncClient, auth_methods

URL = "https://toolbox-zxwxgw5sma-uc.a.run.app"
auth_token_provider = auth_methods.aget_google_id_token(URL)  # can also use sync method

client = ToolboxSyncClient(
    URL,
    client_headers={"Authorization": auth_token_provider},
)

root_agent = Agent(
    name="root_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful AI assistant designed to provide accurate and useful information.",
    tools=client.load_toolset(),
)

app = App(root_agent=root_agent, name="myagent")

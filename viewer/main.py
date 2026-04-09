import os
import mesop as me
import pandas as pd
import yaml
import logging
import json

logging.basicConfig(level=logging.INFO)

# Manually enable debug mode to bypass XSRF check if needed
# (e.g. when running in container behind a proxy)
if os.environ.get("MESOP_XSRF_CHECK") == "false":
    try:
        from mesop.runtime import runtime
        runtime().debug_mode = True
    except Exception as e:
        logging.error(f"Failed to enable debug mode: {e}")

try:
    import dashboard
    import conversations
except ImportError:
    # Optional modules could not be imported; continue without them.
    logging.warning(
        "Optional modules 'dashboard', and 'conversations' "
        "could not be imported (absolute or relative)."
    )


def df_to_config(df: pd.DataFrame) -> dict:
    import ast

    original_dict = {}

    for _, row in df.iterrows():
        key_path = row["config"]
        value_str = row["value"]

        try:
            if pd.isna(value_str):
                value = None
            else:
                value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError, TypeError):
            value = value_str

        keys = key_path.split(".")

        current_level = original_dict
        for key in keys[:-1]:
            if key not in current_level:
                current_level[key] = {}
            current_level = current_level[key]

        current_level[keys[-1]] = value

    return original_dict


@me.stateclass
class State:
    selected_directory: str
    selected_tab: str = "Dashboard"
    conversation_index: int = 0
    eval_summaries: str = ""
    eval_id_filter: str = ""
    product_filter: str = ""


def get_results_dir():
    # Check multiple locations for results directory
    results_dir_candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
    ]

    for candidate in results_dir_candidates:
        if os.path.exists(candidate) and os.path.isdir(candidate):
            return candidate

    return results_dir_candidates[1]  # Fallback to default


def get_eval_details(results_dir, dir_name):
    details = {"product": "N/A", "exact_match": "N/A", "llmrater": "N/A", "trajectory_matcher": "N/A", "turn_count": "N/A", "executable": "N/A", "token_consumption": "N/A", "end_to_end_latency": "N/A"}
    
    # Get product
    config_path = os.path.join(results_dir, dir_name, "configs.csv")
    if os.path.exists(config_path):
        try:
            df = pd.read_csv(config_path)
            # Check for both typo and correct spelling
            row = df[df["config"].isin(["experiment_config.poduct_name", "experiment_config.product_name"])]
            if not row.empty:
                details["product"] = str(row["value"].iloc[0])
        except Exception:
            pass
            
    # Get summary metrics
    summary_path = os.path.join(results_dir, dir_name, "summary.csv")
    if os.path.exists(summary_path):
        try:
            df = pd.read_csv(summary_path)
            for _, row in df.iterrows():
                name = row.get("metric_name")
                correct = row.get("correct_results_count", 0)
                total = row.get("total_results_count", 0)
                pct = (correct / total) * 100 if total > 0 else 0
                if name == "exact_match":
                    details["exact_match"] = f"{pct:.0f}%"
                elif name == "llmrater":
                    details["llmrater"] = f"{pct:.0f}%"
                elif name == "trajectory_matcher":
                    details["trajectory_matcher"] = f"{pct:.0f}%"
                elif name == "turn_count":
                    details["turn_count"] = f"{correct:.1f}"
                elif name == "executable":
                    details["executable"] = f"{pct:.0f}%"
                elif name == "token_consumption":
                    details["token_consumption"] = f"{correct:.0f}"
                elif name == "end_to_end_latency":
                    details["end_to_end_latency"] = f"{correct:.0f}"
        except Exception:
            pass
            
    return details


def get_color_for_pct(val_str):
    if not val_str or not val_str.endswith("%"):
        return "#334155"  # Default color
    try:
        val = float(val_str.rstrip("%"))
        if val >= 80:
            return "#16a34a"  # Green
        elif val >= 40:
            return "#ca8a04"  # Yellow
        else:
            return "#dc2626"  # Red
    except Exception:
        return "#334155"


def on_load(e: me.LoadEvent):
    state = me.state(State)
    results_dir = get_results_dir()
    directories = []
    if os.path.exists(results_dir):
        # List directories only
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]

    job_id = me.query_params.get("job_id") or me.query_params.get("jobid")
    if job_id and job_id in directories:
        state.selected_directory = job_id


@me.page(
    path="/",
    title="EvalBench Viewer",
    on_load=on_load,
    security_policy=me.SecurityPolicy(
        dangerously_disable_trusted_types=True,
        cross_origin_opener_policy="same-origin",
    ),
    stylesheets=[
        "data:",
        "/static/custom.css",
    ],
)
def app():
    state = me.state(State)
    results_dir = get_results_dir()

    directories = []
    if os.path.exists(results_dir):
        # List directories only
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]



    def on_title_click(e: me.ClickEvent):
        state.selected_directory = ""
        state.conversation_index = 0

    # Full-width header bar
    with me.box(
        style=me.Style(
            background="#1e293b",
            padding=me.Padding.symmetric(vertical="16px", horizontal="5%"),
            margin=me.Margin(bottom="24px"),
        )
    ):
        me.button(
            "EvalBench Viewer",
            on_click=on_title_click,
            style=me.Style(
                color="#f8fafc",
                font_size="22px",
                font_weight="700",
                letter_spacing="0.5px",
                background="transparent",
                padding=me.Padding.all("0px"),
                margin=me.Margin.all("0px"),
                border=me.Border.all(me.BorderSide(width="0px")),
                text_align="left",
            ),
        )

    # Centered content at 90% browser width
    with me.box(
        style=me.Style(
            width="90%",
            margin=me.Margin.symmetric(horizontal="auto"),
            display="flex",
            flex_direction="column",
            gap="16px",
        )
    ):


        if state.selected_directory:

            def on_tab_change(e: me.ButtonToggleChangeEvent):
                state.selected_tab = e.value

            me.button_toggle(
                value=state.selected_tab,
                buttons=[
                    me.ButtonToggleButton(label="Dashboard", value="Dashboard"),
                    me.ButtonToggleButton(label="Configs", value="Configs"),
                    # me.ButtonToggleButton(label="Evals", value="Evals"),
                    # me.ButtonToggleButton(label="Scores", value="Scores"),
                    me.ButtonToggleButton(
                        label="Conversations", value="Conversations"
                    ),
                    # me.ButtonToggleButton(label="Summary", value="Summary"),
                ],
                on_change=on_tab_change,
            )

            if state.selected_tab == "Dashboard":
                dashboard.dashboard_component(
                    os.path.join(results_dir, state.selected_directory)
                )
            elif state.selected_tab == "Conversations":

                def on_prev_conversation(e: me.ClickEvent):
                    s = me.state(State)
                    if s.conversation_index > 0:
                        s.conversation_index -= 1

                def on_next_conversation(e: me.ClickEvent):
                    s = me.state(State)
                    s.conversation_index += 1

                conversations.conversations_component(
                    os.path.join(results_dir, state.selected_directory),
                    conversation_index=state.conversation_index,
                    on_prev=on_prev_conversation,
                    on_next=on_next_conversation,
                )
            elif state.selected_tab == "Configs":
                config_path = os.path.join(
                    results_dir, state.selected_directory, "configs.csv"
                )
                if os.path.exists(config_path):
                    try:
                        df = pd.read_csv(config_path)
                        config = df_to_config(df)
                        me.code(yaml.dump(config))
                    except Exception as e:
                        me.text(f"Error reading configs.csv: {e}")
                else:
                    me.text(f"configs.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Evals":
                evals_path = os.path.join(
                    results_dir, state.selected_directory, "evals.csv"
                )
                if os.path.exists(evals_path):
                    try:
                        df = pd.read_csv(evals_path)
                        details = get_eval_details(results_dir, state.selected_directory)
                        df.insert(0, "orchestrator", details["orchestrator"])
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading evals.csv: {e}")
                else:
                    me.text(f"evals.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Scores":
                scores_path = os.path.join(
                    results_dir, state.selected_directory, "scores.csv"
                )
                if os.path.exists(scores_path):
                    try:
                        df = pd.read_csv(scores_path)
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading scores.csv: {e}")
                else:
                    me.text(f"scores.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Summary":
                summary_path = os.path.join(
                    results_dir, state.selected_directory, "summary.csv"
                )
                if os.path.exists(summary_path):
                    try:
                        df = pd.read_csv(summary_path)
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading summary.csv: {e}")
                else:
                    me.text(f"summary.csv not found in {state.selected_directory}")
        else:
            with me.box(
                style=me.Style(
                    background="#ffffff",
                    padding=me.Padding.all("24px"),
                    border_radius="12px",
                    border=me.Border.all(
                        me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                    ),
                    box_shadow="0 1px 3px rgba(0,0,0,0.06)",
                    text_align="center",
                    margin=me.Margin(top="16px"),
                )
            ):
                me.text(
                    "Welcome to EvalBench Viewer",
                    style=me.Style(
                        font_size="24px",
                        font_weight="700",
                        color="#1f2937",
                        margin=me.Margin(bottom="8px"),
                    ),
                )
                me.text(
                    f"Found {len(directories)} evaluation runs. Click on an Eval ID in the table below to explore the results.",
                    style=me.Style(
                        font_size="16px",
                        color="#6b7280",
                        margin=me.Margin(bottom="16px"),
                    ),
                )
                if directories:
                    # Compute summaries if empty
                    s = me.state(State)
                    summaries = []
                    if s.eval_summaries:
                        try:
                            summaries = json.loads(s.eval_summaries)
                        except Exception:
                            summaries = []
                    
                    if not summaries:
                        for d in sorted(directories):
                            details = get_eval_details(results_dir, d)
                            summaries.append({
                                "id": d, 
                                "product": details["product"],
                                "exact_match": details["exact_match"],
                                "llmrater": details["llmrater"],
                                "trajectory_matcher": details["trajectory_matcher"],
                                "turn_count": details["turn_count"],
                                "executable": details["executable"],
                                "token_consumption": details["token_consumption"],
                                "end_to_end_latency": details["end_to_end_latency"]
                            })
                        s.eval_summaries = json.dumps(summaries)

                    # Extract unique values for filters from ALL summaries
                    all_summaries = []
                    if s.eval_summaries:
                        try:
                            all_summaries = json.loads(s.eval_summaries)
                        except Exception:
                            all_summaries = []
                    
                    products = sorted(list(set(x["product"] for x in all_summaries if x["product"] != "N/A")))
                    eval_ids = sorted([x["id"] for x in all_summaries])

                    # Apply filters
                    if state.eval_id_filter:
                        summaries = [x for x in summaries if x["id"] == state.eval_id_filter]
                    if state.product_filter:
                        summaries = [x for x in summaries if x["product"] == state.product_filter]

                    # Render filters UI
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="row",
                            gap="16px",
                            margin=me.Margin(top="16px", bottom="16px"),
                        )
                    ):
                        def on_eval_id_filter_change(e: me.SelectSelectionChangeEvent):
                            st = me.state(State)
                            st.eval_id_filter = e.value

                        def on_product_filter_change(e: me.SelectSelectionChangeEvent):
                            st = me.state(State)
                            st.product_filter = e.value

                        me.select(
                            label="Filter by Eval ID",
                            options=[me.SelectOption(label="All", value="")] + [me.SelectOption(label=d, value=d) for d in eval_ids],
                            on_selection_change=on_eval_id_filter_change,
                            value=state.eval_id_filter,
                        )
                        me.select(
                            label="Filter by Product",
                            options=[me.SelectOption(label="All", value="")] + [me.SelectOption(label=p, value=p) for p in products],
                            on_selection_change=on_product_filter_change,
                            value=state.product_filter,
                        )

                    # Render custom table
                    with me.box(
                        style=me.Style(
                            max_height="600px",
                            overflow_y="auto",
                            margin=me.Margin(top="16px"),
                            display="table",
                            width="100%",
                            border=me.Border.all(
                                me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                            ),
                            border_radius="8px",
                        )
                    ):
                        # Header row
                        with me.box(
                            style=me.Style(
                                display="table-row",
                                background="#f8fafc",
                                font_weight="bold",
                                color="#475569",
                                font_size="12px",
                                text_transform="uppercase",
                                letter_spacing="0.05em",
                            )
                        ):
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")), width="36ch", white_space="nowrap")):
                                me.text("Eval ID")
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                me.text("Product")

                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")), width="18ch", white_space="nowrap")):
                                me.text("Trajectory Matcher")
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                me.text("Turn Count")
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                me.text("Executable")
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                me.text("Token Consumption")
                            with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="12px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                me.text("End-to-End Latency (ms)")

                        # Data rows
                        for idx, item in enumerate(summaries):
                            d = item["id"]
                            prod = item["product"]
                            traj = item.get("trajectory_matcher", "N/A")
                            turns = item.get("turn_count", "N/A")
                            exec_val = item.get("executable", "N/A")
                            tokens = item.get("token_consumption", "N/A")
                            latency = item.get("end_to_end_latency", "N/A")

                            bg_color = "#ffffff" if idx % 2 == 0 else "#f8fafc"

                            def make_on_click(dir_name):
                                def on_click(e: me.ClickEvent):
                                    s = me.state(State)
                                    s.selected_directory = dir_name
                                return on_click

                            with me.box(
                                style=me.Style(
                                    display="table-row",
                                    background=bg_color,
                                )
                            ):
                                # Eval ID as a link/button
                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")), width="36ch", white_space="nowrap")):
                                    me.button(
                                        d,
                                        on_click=make_on_click(d),
                                        style=me.Style(
                                            text_align="center",
                                            background="transparent",
                                            color="#0284c7",
                                            font_family="monospace",
                                            font_size="14px",
                                            padding=me.Padding.all("0px"),
                                            margin=me.Margin.all("0px"),
                                            border=me.Border.all(me.BorderSide(width="0px")),
                                            font_weight="500",
                                            width="100%",
                                        ),
                                    )
                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                    me.text(prod, style=me.Style(color="#334155"))

                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")), width="18ch", white_space="nowrap")):
                                    me.text(traj, style=me.Style(color=get_color_for_pct(traj)))

                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                    me.text(turns, style=me.Style(color="#334155"))

                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                    me.text(exec_val, style=me.Style(color=get_color_for_pct(exec_val)))

                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                    me.text(tokens, style=me.Style(color="#334155"))

                                with me.box(style=me.Style(display="table-cell", padding=me.Padding.symmetric(vertical="10px", horizontal="16px"), text_align="center", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid")))):
                                    me.text(latency, style=me.Style(color="#334155"))


if __name__ == "__main__":
    me.run(app)

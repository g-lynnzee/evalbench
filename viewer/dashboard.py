import mesop as me


import pandas as pd
import os


def dashboard_component(results_dir: str):
    me.text(
        "Dashboard Summary",
        style=me.Style(
            font_size="24px", font_weight="bold", margin=me.Margin(bottom="20px")
        ),
    )

    summary_path = os.path.join(results_dir, "summary.csv")
    if not os.path.exists(summary_path):
        me.text(f"No summary data found in {results_dir}")
        return

    try:
        df = pd.read_csv(summary_path)
    except Exception as e:
        me.text(f"Error reading summary data: {e}")
        return

    with me.box(
        style=me.Style(
            display="flex",
            flex_direction="column",
            gap="16px",
            width="100%",
            max_width="800px",
        )
    ):
        for _, row in df.iterrows():
            metric = row.get("metric_name", "Unknown")
            correct = row.get("correct_results_count", 0)
            total = row.get("total_results_count", 0)

            pct = (correct / total) * 100 if total > 0 else 0

            with me.box(
                style=me.Style(
                    display="flex",
                    flex_direction="column",
                    gap="8px",
                    background="#ffffff",
                    padding=me.Padding.all("16px"),
                    border_radius="12px",
                    border=me.Border.all(
                        me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                    ),
                    box_shadow="0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06)",
                )
            ):
                with me.box(
                    style=me.Style(
                        display="flex",
                        justify_content="space-between",
                        margin=me.Margin(bottom="4px"),
                    )
                ):
                    me.text(
                        metric,
                        style=me.Style(
                            font_weight="600", font_size="16px", color="#111827"
                        ),
                    )
                    me.text(
                        f"{correct}/{total} ({pct:.1f}%)",
                        style=me.Style(
                            color="#4b5563", font_weight="500", font_size="14px"
                        ),
                    )

                with me.box(
                    style=me.Style(
                        width="100%",
                        height="12px",
                        background="#f3f4f6",
                        border_radius="6px",
                        overflow_x="hidden",
                    )
                ):
                    color = (
                        "#3b82f6"
                        if pct > 50
                        else ("#ef4444" if pct < 20 else "#eab308")
                    )
                    if pct == 100:
                        color = "#10b981"

                    me.box(
                        style=me.Style(
                            width=f"{pct}%",
                            height="100%",
                            background=color,
                            border_radius="6px",
                        )
                    )

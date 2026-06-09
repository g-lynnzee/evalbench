def sanitize_sql(sql: str, dialect: str = None):
    # 1. Common cleanup applied to all dialects
    result = (
        sql.replace(
            "```sql", ""
        )  # required for gemini_1.0_pro, gemini_2.0_flash, gemini_2.5_pro
        .replace(
            "```", ""
        )  # required for gemini_1.0_pro, gemini_2.0_flash, gemini_2.5_pro
        .replace('sql: "', "")
        .replace("\\n", " ")
        .replace("  ", "")
        .replace("google_sql", "")
    )

    # 2. Dialect-specific backslash handling
    if dialect == "googlesql":
        # New behavior: specifically unescape backticks and quotes for GoogleSQL
        result = result.replace("\\`", "`").replace('\\"', '"')
    else:
        # Legacy behavior: blindly strip all backslashes for other dialects
        result = result.replace("\\", "")

    result = result.strip()

    # 3. Existing rule for non-googlesql
    if dialect and dialect != "googlesql":
        result = result.replace("`", "")

    return result
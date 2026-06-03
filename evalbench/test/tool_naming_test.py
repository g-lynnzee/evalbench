"""Unit tests for the canonical MCP tool-naming helper."""

import os
import sys
import unittest

# Make the ``generators`` package importable when the test is run directly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.tool_naming import (
    canonical_tool_name,
    canonicalize_agy_tool_name,
    canonicalize_claude_tool_name,
    canonicalize_gemini_tool_name,
    looks_like_canonical_mcp_name,
    parse_agy_mcp_tool_call,
    parse_claude_mcp_tool_name,
    parse_gemini_mcp_tool_name,
)


class CanonicalToolNameTest(unittest.TestCase):

    def test_joins_server_and_tool(self):
        self.assertEqual(
            canonical_tool_name("cloud-sql", "list_instances"),
            "cloud-sql__list_instances",
        )

    def test_returns_bare_tool_when_no_server(self):
        self.assertEqual(canonical_tool_name("", "Read"), "Read")
        self.assertEqual(canonical_tool_name(None, "Read"), "Read")

    def test_empty_tool_returns_empty(self):
        self.assertEqual(canonical_tool_name("cloud-sql", ""), "")


class ParseClaudeMcpToolNameTest(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(
            parse_claude_mcp_tool_name("mcp__cloud-sql__list_instances"),
            ("cloud-sql", "list_instances"),
        )

    def test_server_with_underscore(self):
        # Claude allows underscores in server names; only the first ``__``
        # separates server from tool.
        self.assertEqual(
            parse_claude_mcp_tool_name("mcp__my_server__do_thing"),
            ("my_server", "do_thing"),
        )

    def test_tool_with_double_underscore_preserved(self):
        self.assertEqual(
            parse_claude_mcp_tool_name("mcp__srv__odd__tool"),
            ("srv", "odd__tool"),
        )

    def test_rejects_missing_prefix(self):
        self.assertIsNone(parse_claude_mcp_tool_name("list_instances"))

    def test_rejects_empty_server(self):
        self.assertIsNone(parse_claude_mcp_tool_name("mcp____tool"))

    def test_rejects_no_tool(self):
        self.assertIsNone(parse_claude_mcp_tool_name("mcp__server__"))


class ParseGeminiMcpToolNameTest(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(
            parse_gemini_mcp_tool_name("mcp_cloud-sql_list_instances"),
            ("cloud-sql", "list_instances"),
        )

    def test_tool_with_underscores(self):
        # Server has no underscore by upstream contract; tool may contain
        # several.
        self.assertEqual(
            parse_gemini_mcp_tool_name("mcp_alloydb_create_user_password"),
            ("alloydb", "create_user_password"),
        )

    def test_rejects_missing_prefix(self):
        self.assertIsNone(parse_gemini_mcp_tool_name("list_instances"))

    def test_rejects_server_only(self):
        self.assertIsNone(parse_gemini_mcp_tool_name("mcp_cloudsql"))


class CanonicalizeAdapterFormsTest(unittest.TestCase):

    def test_claude_mcp_becomes_canonical(self):
        self.assertEqual(
            canonicalize_claude_tool_name("mcp__cloud-sql__list_instances"),
            "cloud-sql__list_instances",
        )

    def test_claude_native_tool_passthrough(self):
        self.assertEqual(canonicalize_claude_tool_name("Read"), "Read")
        self.assertEqual(canonicalize_claude_tool_name("Bash"), "Bash")

    def test_claude_malformed_mcp_returned_as_is(self):
        # Falls back to the raw name so callers can debug unexpected inputs
        # instead of silently producing a misleading canonical form.
        self.assertEqual(
            canonicalize_claude_tool_name("mcp__only-server"),
            "mcp__only-server",
        )

    def test_gemini_mcp_becomes_canonical(self):
        self.assertEqual(
            canonicalize_gemini_tool_name("mcp_cloud-sql_list_instances"),
            "cloud-sql__list_instances",
        )

    def test_gemini_native_tool_passthrough(self):
        self.assertEqual(canonicalize_gemini_tool_name("write_file"), "write_file")

    def test_gemini_malformed_mcp_returned_as_is(self):
        self.assertEqual(
            canonicalize_gemini_tool_name("mcp_lonely"),
            "mcp_lonely",
        )


class ParseAgyMcpToolCallTest(unittest.TestCase):
    """agy wraps MCP calls in a native ``call_mcp_tool`` tool whose args
    carry the real server/tool identity. Values are stored JSON-encoded
    (with surrounding quotes), as observed in a real v1.0.3 transcript."""

    # Exact shape captured from a real agy transcript tool_call.
    REAL_ARGS = {
        "Arguments": '{"project":"example-project"}',
        "ServerName": '"cloud-sql"',
        "ToolName": '"list_instances"',
        "toolAction": '"Listing Cloud SQL instances"',
        "toolSummary": '"List Cloud SQL instances"',
    }

    def test_parses_json_quoted_values(self):
        self.assertEqual(
            parse_agy_mcp_tool_call("call_mcp_tool", self.REAL_ARGS),
            ("cloud-sql", "list_instances"),
        )

    def test_canonical_form_strips_quotes(self):
        self.assertEqual(
            canonicalize_agy_tool_name("call_mcp_tool", self.REAL_ARGS),
            "cloud-sql__list_instances",
        )

    def test_unquoted_values_also_work(self):
        self.assertEqual(
            parse_agy_mcp_tool_call(
                "call_mcp_tool",
                {"ServerName": "alloydb", "ToolName": "create_user"},
            ),
            ("alloydb", "create_user"),
        )

    def test_only_canonical_keys_are_accepted(self):
        # The agy v1.0.3 schema uses exactly ``ServerName``/``ToolName`` (no
        # ``json:`` tags, so the Go field names are the property names). Any
        # other casing is not a real agy key and must not be accepted.
        self.assertIsNone(
            parse_agy_mcp_tool_call(
                "call_mcp_tool",
                {"server_name": "cloud-sql", "tool_name": "get_instance"},
            )
        )

    def test_native_tool_is_not_mcp(self):
        self.assertIsNone(parse_agy_mcp_tool_call("run_command", {"Command": "ls"}))
        self.assertIsNone(parse_agy_mcp_tool_call("view_file", {}))

    def test_native_tool_passthrough(self):
        self.assertEqual(canonicalize_agy_tool_name("run_command", {}), "run_command")
        self.assertEqual(canonicalize_agy_tool_name("view_file"), "view_file")

    def test_wrapper_missing_identity_returned_as_is(self):
        # No usable server/tool -> keep the raw wrapper name for debugging.
        self.assertEqual(
            canonicalize_agy_tool_name("call_mcp_tool", {"Arguments": "{}"}),
            "call_mcp_tool",
        )

    def test_agy_does_not_use_gemini_underscore_form(self):
        # agy never emits ``mcp_<server>_<tool>``; such a name is a native
        # tool name as far as agy is concerned and must pass through.
        self.assertEqual(
            canonicalize_agy_tool_name("mcp_cloud-sql_list_instances", {}),
            "mcp_cloud-sql_list_instances",
        )


class LooksLikeCanonicalMcpNameTest(unittest.TestCase):

    def test_mcp_form_is_detected(self):
        self.assertTrue(looks_like_canonical_mcp_name("cloud-sql__list_instances"))
        self.assertTrue(looks_like_canonical_mcp_name("alloydb__create_user"))

    def test_native_tools_rejected(self):
        self.assertFalse(looks_like_canonical_mcp_name("Read"))
        self.assertFalse(looks_like_canonical_mcp_name("update_topic"))
        self.assertFalse(looks_like_canonical_mcp_name("run_shell_command"))

    def test_empty_segments_rejected(self):
        self.assertFalse(looks_like_canonical_mcp_name(""))
        self.assertFalse(looks_like_canonical_mcp_name("__tool"))
        self.assertFalse(looks_like_canonical_mcp_name("server__"))


if __name__ == "__main__":
    unittest.main()

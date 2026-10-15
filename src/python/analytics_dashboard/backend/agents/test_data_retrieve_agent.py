import asyncio
import json
import unittest
from unittest.mock import patch

from agents import data_retrieve_agent


class RunMcpLoopPermissionTests(unittest.TestCase):
    @patch("agents.data_retrieve_agent._call_mcp_tool")
    @patch("agents.data_retrieve_agent.call_llm")
    def test_permission_denial_stops_the_react_loop(self, call_llm, call_mcp_tool):
        query = "SELECT * FROM retail.orders"
        call_llm.return_value = json.dumps({
            "thought": "Run the requested query.",
            "action": "execute_query",
            "query": query,
        })
        call_mcp_tool.return_value = "Query Results:\n" + json.dumps({
            "error": "PostgreSQL query failed: permission denied for table orders",
            "results": [],
        })

        result = asyncio.run(data_retrieve_agent.run_mcp_loop("Get sales", "user-1"))

        self.assertEqual(result["status"], "permission_denied")
        self.assertEqual(result["data"], data_retrieve_agent.PERMISSION_DENIED_MESSAGE)
        self.assertEqual(result["queries"], [])
        call_llm.assert_called_once()
        call_mcp_tool.assert_called_once()

    @patch("agents.data_retrieve_agent._call_mcp_tool")
    @patch("agents.data_retrieve_agent.call_llm")
    def test_empty_query_results_are_not_treated_as_permission_errors(self, call_llm, call_mcp_tool):
        query = "SELECT * FROM retail.orders WHERE store_id = 1"
        call_llm.side_effect = [
            json.dumps({
                "thought": "Run the requested query.",
                "action": "execute_query",
                "query": query,
            }),
            json.dumps({
                "thought": "The query completed.",
                "action": "finish",
                "data": "No matching rows.",
            }),
        ]
        call_mcp_tool.return_value = "Query Results:\n" + json.dumps({
            "results": [],
            "row_count": 0,
            "columns": [],
            "message": "The query returned no results.",
        })

        result = asyncio.run(data_retrieve_agent.run_mcp_loop("Get sales", "user-1"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["queries"], [query])
        self.assertEqual(call_llm.call_count, 2)

    @patch("agents.data_retrieve_agent._call_mcp_tool")
    @patch("agents.data_retrieve_agent.call_llm")
    def test_other_database_errors_are_not_recorded_as_executed_queries(self, call_llm, call_mcp_tool):
        query = "SELECT * FROM retail.missing_table"
        call_llm.side_effect = [
            json.dumps({
                "thought": "Run the requested query.",
                "action": "execute_query",
                "query": query,
            }),
            json.dumps({
                "thought": "Stop after the query failure.",
                "action": "finish",
                "data": "Data unavailable.",
            }),
        ]
        call_mcp_tool.return_value = "Query Results:\n" + json.dumps({
            "error": "PostgreSQL query failed: relation does not exist",
            "results": [],
        })

        result = asyncio.run(data_retrieve_agent.run_mcp_loop("Get sales", "user-1"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["queries"], [])
        self.assertEqual(call_llm.call_count, 2)

    def test_permission_error_classifier_does_not_match_empty_results(self):
        self.assertTrue(data_retrieve_agent._is_permission_denied("permission denied for table orders"))
        self.assertTrue(data_retrieve_agent._is_permission_denied("SQLSTATE 42501"))
        self.assertFalse(data_retrieve_agent._is_permission_denied("The query returned no results."))

    def test_store_access_is_limited_to_the_current_user(self):
        output = "Query Results:\n" + json.dumps({
            "results": [
                {"store_id": 1, "store_name": "Seattle", "rls_user_id": "user-1"},
                {"store_id": 2, "store_name": "Bellevue", "rls_user_id": "user-2"},
            ],
            "row_count": 2,
        })

        records, accessible_stores = data_retrieve_agent._parse_store_access(output, "user-1")

        self.assertEqual(len(records), 2)
        self.assertEqual(accessible_stores, ["Seattle"])

    def test_super_manager_has_all_stores_in_access_scope(self):
        output = "Query Results:\n" + json.dumps({
            "results": [
                {"store_id": 1, "store_name": "Seattle", "rls_user_id": "user-1"},
                {"store_id": 2, "store_name": "Bellevue", "rls_user_id": "user-2"},
            ],
            "row_count": 2,
        })

        _, accessible_stores = data_retrieve_agent._parse_store_access(
            output,
            data_retrieve_agent._SUPER_MANAGER_UUID,
        )

        self.assertEqual(accessible_stores, ["Seattle", "Bellevue"])


if __name__ == "__main__":
    unittest.main()

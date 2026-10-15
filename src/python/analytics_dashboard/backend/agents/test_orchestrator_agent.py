import asyncio
import json
import unittest
from unittest.mock import patch

from agents import orchestrator_agent


class PlanDataTasksTests(unittest.TestCase):
    @patch("agents.orchestrator_agent.call_llm")
    def test_planner_receives_previous_findings_and_completed_tasks(self, call_llm):
        call_llm.return_value = json.dumps(
            {"investigation_complete": False, "tasks": ["Compare category trends by store"]}
        )

        plan = orchestrator_agent.plan_data_tasks(
            "Investigate declining retail sales",
            '{"required_metrics": ["sales"]}',
            previous_findings="Power Tools explains most of the decline.",
            completed_tasks=["Compare annual sales by store"],
        )

        self.assertEqual(plan["tasks"], ["Compare category trends by store"])
        planner_input = call_llm.call_args.args[0][1]["content"]
        self.assertIn("Power Tools explains most of the decline.", planner_input)
        self.assertIn("Compare annual sales by store", planner_input)

    @patch("agents.orchestrator_agent.call_llm")
    def test_planner_rejects_incomplete_plan_without_tasks(self, call_llm):
        call_llm.return_value = '{"investigation_complete": false, "tasks": []}'

        with self.assertRaisesRegex(ValueError, "must include at least one task"):
            orchestrator_agent.plan_data_tasks("Question", "Requirements")

        self.assertEqual(call_llm.call_count, 2)

    @patch("agents.orchestrator_agent.call_llm")
    def test_planner_retries_invalid_tasks_array(self, call_llm):
        call_llm.side_effect = [
            '{"investigation_complete": false, "tasks": null}',
            '{"investigation_complete": false, "tasks": ["Compare sales by store"]}',
        ]

        plan = orchestrator_agent.plan_data_tasks("Question", "Requirements")

        self.assertEqual(plan["tasks"], ["Compare sales by store"])
        retry_messages = call_llm.call_args.args[0]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertIn('"tasks": null', retry_messages[-2]["content"])
        self.assertIn("failed validation", retry_messages[-1]["content"])

    @patch("agents.orchestrator_agent.call_llm")
    def test_planner_receives_authorized_store_scope(self, call_llm):
        call_llm.return_value = '{"investigation_complete": false, "tasks": ["Compare Seattle sales"]}'

        orchestrator_agent.plan_data_tasks(
            "Investigate sales",
            '{"required_metrics": ["sales"]}',
            accessible_stores=["Seattle"],
        )

        planner_input = call_llm.call_args.args[0][1]["content"]
        self.assertIn("Authorized stores for this user: Seattle", planner_input)
        self.assertIn("Do not plan or request data for any other store.", planner_input)


class AdaptiveInvestigationTests(unittest.TestCase):
    def test_second_round_is_planned_from_first_round_results(self):
        plans = [
            {"investigation_complete": False, "tasks": ["Compare annual sales by store"]},
            {"investigation_complete": False, "tasks": ["Compare category trends by store"]},
            {"investigation_complete": True, "tasks": []},
        ]
        planner_inputs = []
        swarm_calls = []

        def plan_tasks(prompt, requirements, previous_findings="", completed_tasks=None):
            planner_inputs.append((previous_findings, list(completed_tasks or [])))
            return plans.pop(0)

        async def run_swarm(tasks, rls_user_id, context):
            swarm_calls.append(tasks)
            if len(swarm_calls) == 1:
                return [{"data": "Power Tools explains most of the decline.", "queries": ["SELECT 1"]}]
            return [{"data": "Seattle and Bellevue declined; Redmond grew.", "queries": ["SELECT 2"]}]

        with (
            patch("agents.orchestrator_agent.plan_data_tasks", side_effect=plan_tasks),
            patch("agents.orchestrator_agent._run_swarm", side_effect=run_swarm),
        ):
            result = asyncio.run(
                orchestrator_agent._run_adaptive_investigation(
                    "Investigate the sales decline",
                    '{"required_metrics": ["sales"]}',
                    "user-1",
                )
            )

        self.assertEqual(swarm_calls, [["Compare annual sales by store"], ["Compare category trends by store"]])
        self.assertIn("Power Tools explains most of the decline.", planner_inputs[1][0])
        self.assertEqual(planner_inputs[1][1], ["Compare annual sales by store"])
        self.assertIn("Seattle and Bellevue declined; Redmond grew.", result["data"])
        self.assertEqual(result["queries"], ["SELECT 1", "SELECT 2"])

    def test_investigation_stops_at_configured_round_limit(self):
        planner_calls = []
        swarm_calls = []

        def plan_tasks(prompt, requirements, previous_findings="", completed_tasks=None):
            planner_calls.append(previous_findings)
            return {"investigation_complete": False, "tasks": [f"Follow-up {len(planner_calls)}"]}

        async def run_swarm(tasks, rls_user_id, context):
            swarm_calls.append(tasks)
            return [{"data": f"Findings for {tasks[0]}", "queries": []}]

        with (
            patch("agents.orchestrator_agent.plan_data_tasks", side_effect=plan_tasks),
            patch("agents.orchestrator_agent._run_swarm", side_effect=run_swarm),
        ):
            result = asyncio.run(
                orchestrator_agent._run_adaptive_investigation(
                    "Investigate retail performance",
                    '{"required_metrics": ["sales"]}',
                    "user-1",
                )
            )

        self.assertEqual(len(planner_calls), orchestrator_agent._MAX_INVESTIGATION_ROUNDS)
        self.assertEqual(len(swarm_calls), orchestrator_agent._MAX_INVESTIGATION_ROUNDS)
        self.assertIn("reaching the configured limit", result["data"])

    def test_permission_denial_stops_additional_investigation_rounds(self):
        planner_calls = []

        def plan_tasks(prompt, requirements, previous_findings="", completed_tasks=None):
            planner_calls.append(prompt)
            return {"investigation_complete": False, "tasks": ["Compare Bellevue sales"]}

        async def run_swarm(tasks, rls_user_id, context):
            return [{
                "status": "permission_denied",
                "data": orchestrator_agent.PERMISSION_DENIED_MESSAGE,
                "queries": [],
            }]

        with (
            patch("agents.orchestrator_agent.plan_data_tasks", side_effect=plan_tasks),
            patch("agents.orchestrator_agent._run_swarm", side_effect=run_swarm),
        ):
            result = asyncio.run(
                orchestrator_agent._run_adaptive_investigation(
                    "Investigate Bellevue sales",
                    '{"required_metrics": ["sales"]}',
                    "user-1",
                )
            )

        self.assertEqual(len(planner_calls), 1)
        self.assertEqual(result["error"], orchestrator_agent.PERMISSION_DENIED_MESSAGE)
        self.assertEqual(result["queries"], [])


class PermissionPreflightTests(unittest.TestCase):
    @patch("agents.orchestrator_agent.generate_report_outline")
    @patch("agents.orchestrator_agent.fetch_metadata")
    def test_inaccessible_explicit_store_is_rejected_before_report_generation(
        self,
        fetch_metadata,
        generate_report_outline,
    ):
        fetch_metadata.return_value = (
            "Accessible stores: Seattle",
            ["Zava Retail Seattle", "Zava Retail Bellevue"],
            ["Zava Retail Seattle"],
        )

        result = orchestrator_agent.run_generation_pipeline(
            [{"role": "user", "content": "Show Bellevue sales"}],
            "Seattle Manager",
            "user-1",
            "/tmp/reports",
        )

        self.assertIn("You do not have access to 'Zava Retail Bellevue'", result["error"])
        self.assertIn("You can generate reports for: 'Zava Retail Seattle'", result["error"])
        generate_report_outline.assert_not_called()

    @patch("agents.orchestrator_agent.generate_report_outline")
    @patch("agents.orchestrator_agent.fetch_metadata")
    def test_short_store_name_in_prompt_is_detected(
        self,
        fetch_metadata,
        generate_report_outline,
    ):
        """A user asking for 'Seattle' must be blocked even though the store's
        full name is 'Zava Retail Seattle'."""
        fetch_metadata.return_value = (
            "Accessible stores: Tacoma",
            ["Zava Retail Seattle", "Zava Retail Tacoma"],
            ["Zava Retail Tacoma"],
        )

        result = orchestrator_agent.run_generation_pipeline(
            [{"role": "user", "content": "Give me the Seattle results for March 2023"}],
            "Tacoma Manager",
            "user-1",
            "/tmp/reports",
        )

        self.assertIn("You do not have access to 'Zava Retail Seattle'", result["error"])
        self.assertIn("You can generate reports for: 'Zava Retail Tacoma'", result["error"])
        generate_report_outline.assert_not_called()

    @patch("agents.orchestrator_agent.generate_report_outline")
    @patch("agents.orchestrator_agent.fetch_metadata")
    def test_multiple_inaccessible_stores_are_all_named(
        self,
        fetch_metadata,
        generate_report_outline,
    ):
        fetch_metadata.return_value = (
            "Accessible stores: Seattle",
            ["Zava Retail Seattle", "Zava Retail Bellevue", "Zava Retail Tacoma", "Zava Retail Online"],
            ["Zava Retail Seattle"],
        )

        result = orchestrator_agent.run_generation_pipeline(
            [{"role": "user", "content": "Compare Bellevue and Tacoma sales"}],
            "Seattle Manager",
            "user-1",
            "/tmp/reports",
        )

        self.assertIn("You do not have access to 'Zava Retail Bellevue', 'Zava Retail Tacoma'", result["error"])
        self.assertIn("You can generate reports for: 'Zava Retail Seattle'", result["error"])
        generate_report_outline.assert_not_called()

    @patch("agents.orchestrator_agent.generate_report_outline")
    @patch("agents.orchestrator_agent.fetch_metadata")
    def test_no_accessible_stores_returns_clear_message(
        self,
        fetch_metadata,
        generate_report_outline,
    ):
        fetch_metadata.return_value = (
            "No accessible stores",
            ["Zava Retail Seattle", "Zava Retail Bellevue"],
            [],
        )

        result = orchestrator_agent.run_generation_pipeline(
            [{"role": "user", "content": "Show Seattle sales"}],
            "Unknown Manager",
            "user-1",
            "/tmp/reports",
        )

        self.assertIn("does not have access to any store data", result["error"])
        generate_report_outline.assert_not_called()


if __name__ == "__main__":
    unittest.main()

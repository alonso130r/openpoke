import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import AgentRoster


class ExecutionSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.roster = AgentRoster(directory / "roster.json")
        self.roster.add_agent("Jai")
        self.roster.update_summary("Jai", "Drafted Jai's renewal waiver.")
        self.logs = ExecutionAgentLogStore(directory / "logs")
        agent = "server.agents.execution_agent.agent"
        runtime = "server.agents.execution_agent.runtime"
        stack.enter_context(patch(f"{agent}.get_execution_agent_logs", return_value=self.logs))
        stack.enter_context(patch("server.services.execution.roster._agent_roster", self.roster))
        stack.enter_context(patch(
            f"{runtime}.get_settings",
            return_value=SimpleNamespace(openrouter_api_key="test", execution_agent_model="test"),
        ))
        stack.enter_context(patch(f"{runtime}.get_tool_registry", return_value={}))

    async def execute(self, text, *, name="Jai", error=None):
        runtime = ExecutionAgentRuntime(name)
        model = AsyncMock(
            return_value={"choices": [{"message": {"content": text}}]},
            side_effect=error,
        )
        with patch.object(runtime, "_make_llm_call", model):
            result = await runtime.execute("Check the draft.")
        return result, model.call_args.args[0]

    async def test_summary_persists_without_entering_log_or_callback(self):
        self.logs.record_agent_response("Jai", "Saved renewal draft draft-123.")
        self.logs.record_agent_response("Other worker", "Saved unrelated draft draft-456.")
        summary = "Drafted Jai's renewal waiver and checked its status."
        result, prompt = await self.execute(
            f"The draft is still pending.\n<routing_summary>{summary}</routing_summary>"
        )

        self.assertTrue(result.success)
        self.assertIn("Drafted Jai's renewal waiver.", prompt)
        self.assertIn("Saved renewal draft draft-123.", prompt)
        self.assertNotIn("draft-456", prompt)
        self.assertEqual(self.roster.get_records()[0]["summary"], summary)
        self.assertEqual(result.response, "The draft is still pending.")
        self.assertNotIn(summary, self.logs.load_transcript("Jai"))
        self.assertEqual(
            ExecutionBatchManager()._format_batch_payload([result]),
            "[SUCCESS] Jai: The draft is still pending.",
        )

        _, next_prompt = await self.execute("Still pending.")
        self.assertIn(summary, next_prompt)

    async def test_missing_or_invalid_summary_keeps_previous_value(self):
        for suffix in ("", "\n<routing_summary>First line\nSecond line</routing_summary>",
                       "\n<routing_summary>Unclosed", "\n<routing_summary></routing_summary>"):
            with self.subTest(suffix=suffix):
                result, _ = await self.execute("Still pending." + suffix)
                self.assertTrue(result.success)
                self.assertEqual(result.response, "Still pending.")
                self.assertEqual(self.roster.get_records()[0]["summary"], "Drafted Jai's renewal waiver.")

    async def test_failed_execution_and_failed_write_preserve_summary(self):
        result, _ = await self.execute("", error=RuntimeError("Model unavailable"))
        self.assertFalse(result.success)

        with patch.object(self.roster, "update_summary", side_effect=OSError("Disk unavailable")):
            result, _ = await self.execute(
                "Done.\n<routing_summary>Checked the waiver draft.</routing_summary>"
            )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "Done.")
        self.assertEqual(self.roster.get_records()[0]["summary"], "Drafted Jai's renewal waiver.")

    async def test_unregistered_worker_does_not_create_roster_entry(self):
        result, _ = await self.execute(
            "Done.\n<routing_summary>Checked a scheduled task.</routing_summary>",
            name="Scheduled worker",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "Done.")
        self.assertEqual(self.roster.get_agents(), ["Jai"])

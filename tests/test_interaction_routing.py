import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.execution_agent.runtime import ExecutionResult
from server.agents.interaction_agent import agent, runtime, tools
from server.services.execution.roster import AgentRoster


class InteractionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_retry_dispatch_and_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            roster = AgentRoster(Path(directory) / "roster.json")
            roster.add_agent("Jai")
            roster.update_summary("Jai", "Drafted the renewal waiver.")
            roster.add_agent("Unrelated worker")
            roster.update_summary("Unrelated worker", "Prepared a proposal.")
            log = Mock()
            log.load_transcript.return_value = ""
            callback = asyncio.Event()

            async def respond(prompt, messages):
                initial = messages[0]["content"]
                self.assertNotIn("<active_agents>", initial)
                self.assertNotIn("Unrelated worker", initial)
                if "<new_agent_message>" in initial:
                    self.assertIn("[SUCCESS] Jai: Draft found.", initial)
                    callback.set()
                    message = {"content": "I found the draft."}
                else:
                    tool_results = [item for item in messages if item["role"] == "tool"]
                    steps = [
                        ("search_execution_agents", {"query": "exception"}),
                        ("search_execution_agents", {"query": "waiver"}),
                        ("send_message_to_agent", {
                            "mode": "reuse", "agent_name": "Jai", "instructions": "Find the draft."
                        }),
                    ]
                    index = len(tool_results)
                    if index == 1:
                        self.assertIn('"matches": []', tool_results[-1]["content"])
                    if index == 2:
                        self.assertIn("Drafted the renewal waiver.", tool_results[-1]["content"])
                    if index < len(steps):
                        name, arguments = steps[index]
                        message = {"tool_calls": [{
                            "id": str(index), "function": {"name": name, "arguments": arguments}
                        }]}
                    else:
                        message = {"content": "Checking."}
                return {"choices": [{"message": message}]}

            settings = SimpleNamespace(
                openrouter_api_key="test", interaction_agent_model="test", summarization_enabled=False
            )
            worker = AsyncMock()
            worker.execute.return_value = ExecutionResult("Jai", True, "Draft found.")
            manager = ExecutionBatchManager()
            with (
                patch.object(tools, "get_agent_roster", return_value=roster),
                patch.object(agent, "get_agent_roster", return_value=roster),
                patch.object(tools, "get_execution_agent_logs", return_value=log),
                patch.object(tools, "_EXECUTION_BATCH_MANAGER", manager),
                patch.object(runtime, "get_settings", return_value=settings),
                patch.object(runtime, "get_conversation_log", return_value=log),
                patch.object(runtime.InteractionAgentRuntime, "_make_llm_call", side_effect=respond),
                patch("server.agents.execution_agent.batch_manager.ExecutionAgentRuntime", return_value=worker),
            ):
                unknown = tools.handle_tool_call("send_message_to_agent", {
                    "mode": "reuse", "agent_name": "Missing", "instructions": "Check."
                })
                duplicate = tools.handle_tool_call("send_message_to_agent", {
                    "mode": "create", "agent_name": "Jai", "instructions": "Check."
                })
                self.assertFalse(unknown.success)
                self.assertFalse(duplicate.success)
                log.record_request.assert_not_called()
                result = await runtime.InteractionAgentRuntime().execute("Find the exception draft.")
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.execution_agents_used, 1)
                await asyncio.wait_for(callback.wait(), timeout=1)

            log.record_request.assert_called_once_with("Jai", "Find the draft.")
            worker.execute.assert_awaited_once_with("Find the draft.")
            self.assertEqual(roster.get_agents(), ["Jai", "Unrelated worker"])

    async def test_create_claims_name_and_only_schedules_one_job(self):
        with tempfile.TemporaryDirectory() as directory:
            roster = AgentRoster(Path(directory) / "roster.json")
            log = Mock()
            manager = AsyncMock()
            manager.execute_agent.return_value = ExecutionResult("Jai", True, "Done.")
            args = {"mode": "create", "agent_name": "Jai", "instructions": "Check."}

            with (
                patch.object(tools, "get_agent_roster", return_value=roster),
                patch.object(tools, "get_execution_agent_logs", return_value=log),
                patch.object(tools, "_EXECUTION_BATCH_MANAGER", manager),
            ):
                created = tools.handle_tool_call("send_message_to_agent", args)
                duplicate = tools.handle_tool_call("send_message_to_agent", args)
                await asyncio.sleep(0)

            self.assertTrue(created.success)
            self.assertTrue(created.payload["new_agent_created"])
            self.assertFalse(duplicate.success)
            self.assertEqual(roster.get_agents(), ["Jai"])
            log.record_request.assert_called_once_with("Jai", "Check.")
            manager.execute_agent.assert_awaited_once_with("Jai", "Check.")

    def test_user_prompt_includes_only_top_matches_and_callbacks_skip_lookup(self):
        roster = Mock()
        roster.get_records.return_value = [
            {"name": name, "summary": summary}
            for name, summary in [
                ("Archive B", "Reviewed an Acme renewal."),
                ("Archive A", "Reviewed an Acme renewal."),
                ("Archive C", "Reviewed an Acme renewal."),
                ("Waiver", "Drafted an Acme fee waiver."),
                ("Other", "Prepared a proposal."),
            ]
        ]
        with patch.object(agent, "get_agent_roster", return_value=roster) as get_roster:
            prompt = agent.prepare_message_with_history("Show the Acme fee-waiver draft.", "")[0]["content"]
            candidates = json.loads(prompt.split("<candidate_agents>\n")[1].split("\n</candidate_agents>")[0])
            self.assertEqual([match["name"] for match in candidates], ["Waiver", "Archive A", "Archive B"])
            self.assertNotIn("<active_agents>", prompt)
            callback = agent.prepare_message_with_history("Acme fee waiver result.", "", "agent")
            self.assertNotIn("<candidate_agents>", callback[0]["content"])
            get_roster.assert_called_once_with()
            no_match = agent.prepare_message_with_history("Hello.", "")
            self.assertNotIn("<candidate_agents>", no_match[0]["content"])

    async def test_wait_ends_turn_after_other_calls_in_same_response(self):
        settings = SimpleNamespace(
            openrouter_api_key="test", interaction_agent_model="test", summarization_enabled=False
        )
        log = Mock()
        log.load_transcript.return_value = ""
        model = AsyncMock(return_value={"choices": [{"message": {
            "content": "This should not be sent as a fallback.",
            "tool_calls": [
                {"id": "wait", "function": {"name": "wait", "arguments": {"reason": "Pending."}}},
                {"id": "draft", "function": {"name": "send_draft", "arguments": {
                    "to": "jai@example.com", "subject": "Draft", "body": "Hello.",
                }}},
            ],
        }}]})
        with (
            patch.object(runtime, "get_settings", return_value=settings),
            patch.object(runtime, "get_conversation_log", return_value=log),
            patch.object(tools, "get_conversation_log", return_value=log),
            patch.object(runtime.InteractionAgentRuntime, "_make_llm_call", model),
        ):
            result = await runtime.InteractionAgentRuntime().handle_agent_message("Draft ready.")

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.response, "")
        model.assert_awaited_once()
        log.record_wait.assert_called_once_with("Pending.")
        log.record_reply.assert_called_once_with("To: jai@example.com\nSubject: Draft\n\nHello.")

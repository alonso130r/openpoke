import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fake_gmail import FakeGmail
from server.agents.execution_agent import agent, runtime
from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.agents.execution_agent.tools import gmail
from server.agents.interaction_agent import tools
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import AgentRoster


_FIXTURE = Path(__file__).with_name("fixtures") / "routing_mailbox.json"


class SimulatedMailboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    async def test_same_draft_is_recovered_with_short_and_large_rosters(self):
        case = self.fixture["follow_up"]
        expected = next(draft for draft in self.fixture["drafts"] if draft["id"] == case["draft_id"])
        for extra_workers in (0, 100):
            with self.subTest(extra_workers=extra_workers), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                roster = AgentRoster(base / "roster.json")
                logs = ExecutionAgentLogStore(base)
                mailbox = FakeGmail(self.fixture["drafts"])
                for worker in self.fixture["workers"]:
                    roster.add_agent(worker["name"])
                    roster.update_summary(worker["name"], worker["summary"])
                    logs.record_agent_response(worker["name"], f'Saved draft ID {worker["draft_id"]}.')
                for index in range(extra_workers):
                    name = f"Archived worker {index}"
                    roster.add_agent(name)
                    summary = "Reviewed an Acme renewal." if index % 2 else "Prepared a vendor proposal."
                    roster.update_summary(name, summary)

                async def respond(prompt, messages, with_tools):
                    self.assertIn(f'Saved draft ID {case["draft_id"]}.', prompt)
                    self.assertNotIn("Saved draft ID acme-deadline-2027.", prompt)
                    if messages[-1]["role"] == "tool":
                        data = json.loads(messages[-1]["content"])["result"]["data"]
                        draft = next(item for item in data["drafts"] if item["id"] == case["draft_id"])
                        message = {
                            "content": f'{draft["id"]} | {draft["recipient_email"]} | {draft["body"]}'
                        }
                    else:
                        message = {"tool_calls": [{
                            "id": "list-drafts",
                            "function": {"name": "gmail_list_drafts", "arguments": '{"verbose": true}'},
                        }]}
                    return {"choices": [{"message": message}]}

                manager = ExecutionBatchManager()
                callback = asyncio.get_running_loop().create_future()

                async def capture(payload):
                    callback.set_result(payload)

                settings = SimpleNamespace(openrouter_api_key="test", execution_agent_model="test")
                with (
                    patch.object(tools, "get_agent_roster", return_value=roster),
                    patch.object(tools, "get_execution_agent_logs", return_value=logs),
                    patch.object(tools, "_EXECUTION_BATCH_MANAGER", manager),
                    patch.object(agent, "get_agent_roster", return_value=roster),
                    patch.object(agent, "get_execution_agent_logs", return_value=logs),
                    patch.object(runtime, "get_settings", return_value=settings),
                    patch.object(runtime.ExecutionAgentRuntime, "_make_llm_call", side_effect=respond),
                    patch.object(gmail, "_LOG_STORE", logs),
                    patch.object(gmail, "get_active_gmail_user_id", return_value=mailbox.user_id),
                    patch.object(gmail, "execute_gmail_tool", side_effect=mailbox.execute),
                    patch.object(manager, "_dispatch_to_interaction_agent", side_effect=capture),
                ):
                    lookup = tools.handle_tool_call("search_execution_agents", {"query": case["query"]})
                    matches = lookup.payload["matches"]
                    self.assertEqual(matches[0]["name"], case["worker"])
                    self.assertEqual(len(matches), 2 if extra_workers else 3)
                    dispatch = tools.handle_tool_call("send_message_to_agent", {
                        "mode": "reuse",
                        "agent_name": matches[0]["name"],
                        "instructions": case["instructions"],
                    })
                    self.assertTrue(dispatch.success, dispatch.payload)
                    result = await asyncio.wait_for(callback, timeout=2)

                self.assertEqual(
                    result,
                    f'[SUCCESS] {case["worker"]}: {expected["id"]} | '
                    f'{expected["recipient_email"]} | {expected["body"]}',
                )
                self.assertEqual(mailbox.calls, [("GMAIL_LIST_DRAFTS", mailbox.user_id, {"verbose": True})])
                self.assertFalse(mailbox.sent)
                self.assertEqual(list(mailbox.drafts.values()), self.fixture["drafts"])

    async def test_draft_writes_are_recorded_and_a_new_mailbox_resets_them(self):
        mailbox = FakeGmail(self.fixture["drafts"])
        with (
            patch.object(gmail, "_LOG_STORE", Mock()),
            patch.object(gmail, "get_active_gmail_user_id", return_value=mailbox.user_id),
            patch.object(gmail, "execute_gmail_tool", side_effect=mailbox.execute),
        ):
            created = gmail.gmail_create_draft("jai@example.com", "Renewal", "Could you take a look?")
            draft = created["data"]
            self.assertIn(draft["id"], mailbox.drafts)
            gmail.gmail_execute_draft(draft["id"])

        self.assertEqual(mailbox.sent, [draft])
        self.assertNotIn(draft["id"], mailbox.drafts)
        self.assertEqual(mailbox.calls, [
            ("GMAIL_CREATE_EMAIL_DRAFT", mailbox.user_id, {
                "recipient_email": "jai@example.com", "subject": "Renewal", "body": "Could you take a look?",
            }),
            ("GMAIL_SEND_DRAFT", mailbox.user_id, {"draft_id": draft["id"]}),
        ])
        reset = FakeGmail(self.fixture["drafts"])
        self.assertEqual(list(reset.drafts.values()), self.fixture["drafts"])
        self.assertFalse(reset.calls)
        self.assertFalse(reset.sent)

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prepare_routing_eval import SETUP, prepare_case
from server.agents.execution_agent import runtime


class PrepareRoutingEvalTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_keeps_follow_up_hidden_and_preserves_missing_summary(self):
        case = json.loads(SETUP.read_text())[0]

        async def respond(prompt, messages, with_tools):
            self.assertNotIn(case["follow_up"], prompt)
            self.assertNotIn(case["follow_up"], messages[0]["content"])
            if messages[-1]["role"] == "tool":
                draft_id = json.loads(messages[-1]["content"])["result"]["data"]["id"]
                summary = "\n<routing_summary>Drafted Mina's delivery email.</routing_summary>"
                message = {"content": f"Saved {draft_id}." + (summary if draft_id.endswith("-1") else "")}
            else:
                fields = json.loads(messages[0]["content"].splitlines()[-1])
                message = {"tool_calls": [{
                    "id": "create", "function": {
                        "name": "gmail_create_draft", "arguments": json.dumps(fields),
                    },
                }]}
            return {"choices": [{"message": message}]}

        settings = SimpleNamespace(openrouter_api_key="test", execution_agent_model="test")
        with (
            patch.object(runtime, "get_settings", return_value=settings),
            patch.object(runtime.ExecutionAgentRuntime, "_make_llm_call", side_effect=respond),
        ):
            snapshot = await prepare_case(case, "test")

        self.assertEqual(snapshot["instructions"], case["follow_up"])
        self.assertEqual(snapshot["draft_id"], "simulated-draft-1")
        self.assertIn("[SUCCESS] Mina delivery schedule draft:", snapshot["context"])
        self.assertEqual(snapshot["missing_summaries"], ["Mina invoice schedule draft"])
        first, second = snapshot["mailbox"]["workers"]
        self.assertEqual(first["summary"], "Drafted Mina's delivery email.")
        self.assertIn("Saved simulated-draft-1.", first["execution_log"])
        self.assertIsNone(second["summary"])

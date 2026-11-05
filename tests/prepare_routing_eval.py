"""Generate worker summaries and histories for a separate routing evaluation."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fake_gmail import FakeGmail
from run_routing_eval import ROOT, read_key


SETUP = Path(__file__).with_name("fixtures") / "generated_summary_setup.json"


async def prepare_case(case, model):
    from server.agents.execution_agent import runtime
    from server.agents.execution_agent.tools import gmail
    from server.services.execution import log_store, roster

    mailbox = FakeGmail([])
    fields = ("recipient_email", "subject", "body")
    workers = []
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        agents = roster.AgentRoster(base / "roster.json")
        logs = log_store.ExecutionAgentLogStore(base / "execution")
        with (
            patch.object(roster, "_agent_roster", agents),
            patch.object(log_store, "_execution_agent_logs", logs),
            patch.object(gmail, "_LOG_STORE", logs),
            patch.object(gmail, "get_active_gmail_user_id", return_value=mailbox.user_id),
            patch.object(gmail, "execute_gmail_tool", side_effect=mailbox.execute),
        ):
            for setup in case["workers"]:
                name = setup["name"]
                agents.add_agent(name)
                expected = {key: setup[key] for key in fields}
                instructions = (
                    "Create exactly one Gmail draft with these exact fields. Leave it unsent. "
                    "Report the saved draft ID and any approval decision in your result.\n"
                    f"{setup.get('decision', '')}\n{json.dumps(expected)}"
                )
                logs.record_request(name, instructions)
                worker = runtime.ExecutionAgentRuntime(name)
                worker.model = model
                start = len(mailbox.calls)
                result = await worker.execute(instructions)
                writes = [call for call in mailbox.calls[start:] if call[0] != "GMAIL_LIST_DRAFTS"]
                matching = [
                    draft for draft in mailbox.drafts.values()
                    if all(draft.get(key) == value for key, value in expected.items())
                ]
                if (
                    not result.success or len(writes) != 1
                    or writes[0][0] != "GMAIL_CREATE_EMAIL_DRAFT" or len(matching) != 1
                    or matching[0]["id"] not in result.response
                ):
                    raise ValueError(f"{case['id']} / {name}: setup did not save and report the expected draft")
                if setup.get("decision") and "finance approved" in setup["decision"].lower():
                    if "approv" not in result.response.casefold() or "finance" not in result.response.casefold():
                        raise ValueError(f"{case['id']} / {name}: setup result omitted the Finance approval")
                summary = next(record["summary"] for record in agents.get_records() if record["name"] == name)
                workers.append({
                    "name": name,
                    "summary": summary,
                    "draft_id": matching[0]["id"],
                    "execution_log": logs._log_path(name).read_text(encoding="utf-8"),
                })
                if len(workers) == 1:
                    recent_result = result.response

    target = workers[0]
    return {
        "id": case["id"],
        "kind": "retrieve",
        "instructions": case["follow_up"],
        "worker": target["name"],
        "draft_id": target["draft_id"],
        "provenance": case.get("provenance", False),
        "context": f"[SUCCESS] {target['name']}: {recent_result}" if case.get("recent") else None,
        "setup_model": model,
        "missing_summaries": [worker["name"] for worker in workers if not worker["summary"]],
        "mailbox": {"drafts": list(mailbox.drafts.values()), "workers": workers},
    }


async def prepare(model):
    cases = json.loads(SETUP.read_text(encoding="utf-8"))
    return [await prepare_case(case, model) for case in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="openai/gpt-6-luna")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Choose a fresh path to preserve the previous snapshot.")
    key = read_key()
    if not key:
        parser.error("Set OPENROUTER_API_KEY in the environment or the OpenPoke .env file.")
    os.environ["OPENROUTER_API_KEY"] = key
    sys.path.insert(0, str(ROOT))
    snapshot = asyncio.run(prepare(args.model))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(snapshot, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()

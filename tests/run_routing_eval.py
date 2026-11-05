"""Compare OpenPoke routing with live models and a local, simulated Gmail inbox."""

import argparse
import asyncio
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

from fake_gmail import FakeGmail


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = "5b5f635"


def roster_distractors(count, extra_topics=()):
    """Create plausible prior work without changing the mailbox fixture."""
    topics = (
        "Acme Billing renewal fee waiver",
        "Acme Support renewal deadline extension",
        "Acme Billing renewal charge dispute",
        "Jai revised proposal",
        "Vijay revised proposal",
        *extra_topics,
    )
    scopes = ("pilot", "enterprise", "regional", "partner", "trial")
    for index in range(count):
        topic = topics[index % len(topics)]
        variant = index // len(topics)
        scope = scopes[variant % len(scopes)]
        year = 2022 + (variant // len(scopes)) % 6
        reference = f"archive-{index + 1}"
        yield {
            "name": f"{topic} {year} {scope} {reference}",
            "summary": f"Reviewed the {year} {scope} {topic} for {reference}.",
        }


def align_baseline_wait(checkout):
    """Apply the current wait contract to the archived baseline only."""
    directory = checkout / "server/agents/interaction_agent"
    runtime_path = directory / "runtime.py"
    source = runtime_path.read_text(encoding="utf-8")
    if "            if wait_requested:\n" not in source:
        edits = (
            ("            for tool_call in parsed_tool_calls:",
             "            wait_requested = False\n            for tool_call in parsed_tool_calls:"),
            ("                result = self._execute_tool(tool_call)\n",
             "                result = self._execute_tool(tool_call)\n"
             '                if result.success and tool_call.name == "wait":\n'
             "                    wait_requested = True\n"),
            ("                messages.append(tool_message)\n        else:",
             "                messages.append(tool_message)\n"
             '            if wait_requested:\n                summary.last_assistant_text = ""\n'
             "                break\n        else:"),
        )
        for old, new in edits:
            if source.count(old) != 1:
                raise ValueError("Cannot align wait behavior for this baseline runtime")
            source = source.replace(old, new, 1)
        runtime_path.write_text(source, encoding="utf-8")

    prompt_path = directory / "system_prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    old = "and ends the current turn after the tool calls in the same response."
    if old not in prompt:
        prompt_path.write_text(prompt.replace(
            "that prevents redundant messages to the user.",
            old + " Execution results arrive in a separate turn.",
            1,
        ), encoding="utf-8")


_DRAFT_HEADERS = re.compile(r"^To:[ \t]*(.+)\r?\nSubject:[ \t]*(.+)", re.IGNORECASE | re.MULTILINE)


def score_draft_replies(replies, expected):
    """Check the requested body and flag conflicting draft blocks."""
    def normalize(text):
        return " ".join(text.casefold().split())

    def unquote(text):
        return re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.MULTILINE)

    def draft_body_matches(body):
        paragraphs = re.split(r"\n[ \t]*\n", unquote(body).strip())
        return any(
            normalize("\n\n".join(paragraphs[:end]).strip().strip('"“”'))
            == expected_body
            for end in range(1, len(paragraphs) + 1)
        )

    expected_body = normalize(expected["body"])
    returned_body = any(expected_body in normalize(unquote(reply)) for reply in replies)
    conflicts = []
    for index, reply in enumerate(replies):
        headers = list(_DRAFT_HEADERS.finditer(reply))
        for position, header in enumerate(headers):
            end = headers[position + 1].start() if position + 1 < len(headers) else len(reply)
            recipient, subject = header.groups()
            body = reply[header.end():end]
            if (
                normalize(recipient) != normalize(expected["recipient_email"])
                or normalize(subject) != normalize(expected["subject"])
                or not draft_body_matches(body)
            ):
                conflicts.append({"reply_index": index, "recipient": recipient, "subject": subject})
    return returned_body, conflicts


def read_key():
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OPENROUTER_API_KEY":
                return value.strip().strip("'\"")
    return None


async def run_case(checkout, version, extra_workers, args, case_id, target_position="default"):
    sys.path.insert(0, str(checkout))

    from server.agents.execution_agent import runtime as execution
    from server.agents.execution_agent.tools import gmail
    from server.agents.interaction_agent import runtime as interaction, tools
    from server.config import get_settings
    from server.services.conversation import log as conversation
    from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog
    from server.services.execution import log_store, roster

    case = next(
        item for item in json.loads(args.cases_file.read_text(encoding="utf-8"))
        if item["id"] == case_id
    )
    fixture = case["mailbox"]
    expected = next(draft for draft in fixture["drafts"] if draft["id"] == case["draft_id"])
    mailbox = FakeGmail(fixture["drafts"])
    calls, routing, interaction_tools = [], [], []
    live_completion = interaction.request_chat_completion
    handle_tool = interaction.handle_tool_call
    settings = get_settings()
    settings.interaction_agent_model = args.interaction_model
    settings.execution_agent_model = args.execution_model
    settings.conversation_summary_threshold = 0

    def redact_error(message):
        key = settings.openrouter_api_key
        return message.replace(key, "[redacted]") if message and key else message

    automatic_shortlist = []

    def track_model(role):
        async def complete(**kwargs):
            call = {"role": role, "model": kwargs["model"], "input_tokens": None}
            if role == "interaction" and not calls:
                prompt = kwargs["messages"][0]["content"]
                match = re.search(r"^<candidate_agents>\n(.*?)\n</candidate_agents>$", prompt, re.MULTILINE | re.DOTALL)
                if match:
                    automatic_shortlist.extend(json.loads(match[1]))
            calls.append(call)
            try:
                result = await live_completion(**kwargs)
            except Exception as exc:
                call["failed"] = True
                call["error"] = redact_error(str(exc))
                raise
            call["input_tokens"] = (result.get("usage") or {}).get("prompt_tokens")
            return result

        return complete

    def track_tool(name, arguments):
        result = handle_tool(name, arguments)
        event = {
            "tool": name, "arguments": arguments,
            "success": result.success, "result": result.payload,
        }
        interaction_tools.append(event)
        if name in ("search_execution_agents", "send_message_to_agent"):
            routing.append(event)
        return result

    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        agents = roster.AgentRoster(base / "roster.json")
        logs = log_store.ExecutionAgentLogStore(base / "execution")
        memory = WorkingMemoryLog(base / "working_memory.log")
        with patch.object(conversation, "_resolve_working_memory_log", return_value=memory):
            chat = conversation.ConversationLog(base / "conversation.log")
        if context := case.get("context"):
            if context.startswith("[SUCCESS]"):
                chat.record_agent_message(context)
            else:
                chat.record_user_message(context)
        extra_topics = {
            "workload-beacon-legal": ("Beacon rollout wording Legal review",),
            "workload-orion-finance": ("Orion renewal Finance exception review",),
            "workload-northstar-security": ("Northstar incident customer update Security review",),
        }.get(case["id"], ())
        workers = [*fixture["workers"], *roster_distractors(extra_workers, extra_topics)]
        if extra_workers:
            random.Random(case["id"]).shuffle(workers)
        if target_position != "default":
            target_worker = next(worker for worker in workers if worker["name"] == case["worker"])
            workers.remove(target_worker)
            position = {"early": 0, "middle": len(workers) // 2, "late": len(workers)}
            workers.insert(position[target_position], target_worker)
        for worker in workers:
            agents.add_agent(worker["name"])
            if version == "lookup":
                agents.update_summary(worker["name"], worker["summary"])
            history = (
                f'Saved draft ID {worker["draft_id"]}.'
                if "draft_id" in worker else worker["summary"]
            )
            if "execution_log" in worker:
                logs._log_path(worker["name"]).write_text(worker["execution_log"], encoding="utf-8")
            else:
                logs.record_agent_response(worker["name"], worker.get("history", history))

        reply_times = []
        record_reply = chat.record_reply

        def timed_reply(message):
            record_reply(message)
            reply_times.append({"seconds": round(time.monotonic() - started, 3), "text": message})

        with (
            patch.object(chat, "record_reply", side_effect=timed_reply),
            patch.object(roster, "_agent_roster", agents),
            patch.object(log_store, "_execution_agent_logs", logs),
            patch.object(interaction, "get_conversation_log", return_value=chat),
            patch.object(interaction, "get_working_memory_log", return_value=memory),
            patch.object(tools, "get_conversation_log", return_value=chat),
            patch.object(gmail, "_LOG_STORE", logs),
            patch.object(gmail, "get_active_gmail_user_id", return_value=mailbox.user_id),
            patch.object(gmail, "execute_gmail_tool", side_effect=mailbox.execute),
            patch.object(interaction, "request_chat_completion", track_model("interaction")),
            patch.object(execution, "request_chat_completion", track_model("execution")),
            patch.object(interaction, "handle_tool_call", side_effect=track_tool),
        ):
            parent = asyncio.current_task()

            async def run_turn():
                result = await interaction.InteractionAgentRuntime().execute(case["instructions"])
                # Dispatch and result callbacks both use create_task. Drain them while
                # the isolated stores and Gmail patch are still installed.
                current = asyncio.current_task()
                while pending := asyncio.all_tasks() - {current, parent}:
                    await asyncio.gather(*pending)
                return result

            started = time.monotonic()
            turn = asyncio.create_task(run_turn())
            try:
                result = await asyncio.wait_for(asyncio.shield(turn), timeout=args.timeout)
            finally:
                pending = asyncio.all_tasks() - {asyncio.current_task()}
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            elapsed = time.monotonic() - started

        entries = list(chat.iter_entries())

    replies = [payload for tag, _, payload in entries if tag == "poke_reply"]
    first_correct_reply_seconds = None
    if expected:
        for reply in reply_times:
            shown, conflicts = score_draft_replies([reply["text"]], expected)
            if shown and not conflicts:
                first_correct_reply_seconds = reply["seconds"]
                break
    writes = [call for call in mailbox.calls if call[0] != "GMAIL_LIST_DRAFTS"]
    interaction_tokens = [call["input_tokens"] for call in calls if call["role"] == "interaction"]
    execution_tokens = [call["input_tokens"] for call in calls if call["role"] == "execution"]
    returned_body, conflicting_drafts = score_draft_replies(replies, expected)
    correct_draft_shown = returned_body and not conflicting_drafts
    success = correct_draft_shown and not writes

    dispatched = [event["result"] for event in routing
                  if event["tool"] == "send_message_to_agent" and event["success"]]
    shortlists = [event["result"]["matches"] for event in routing
                  if event["tool"] == "search_execution_agents" and event["success"]]
    target = case["worker"]
    model_failed = any(call.get("failed") for call in calls)
    exact_dispatch = (
        len(dispatched) == 1 and dispatched[0]["agent_name"] == target
        and not dispatched[0]["new_agent_created"]
        if target and not model_failed else None
    )
    provenance_outcome = None
    if case.get("provenance"):
        if model_failed:
            provenance_outcome = "model_error"
        elif writes:
            provenance_outcome = "unexpected_write"
        elif result.success and success:
            provenance_outcome = "correct_direct" if exact_dispatch else "correct_after_routing_miss"
        elif conflicting_drafts:
            provenance_outcome = "conflicting_draft"
        else:
            provenance_outcome = "review_reply"

    return {
        "case": case["id"],
        "case_kind": case["kind"],
        "target_position": target_position,
        "expected_worker_roster_index": next(
            (index for index, worker in enumerate(workers) if worker["name"] == target), None
        ),
        "exact_dispatch_success": exact_dispatch,
        "provenance_outcome": provenance_outcome,
        "version": version,
        "roster_size": len(fixture["workers"]) + extra_workers,
        "prompt": case["instructions"],
        "expected_worker": case["worker"],
        "expected_draft": case["draft_id"],
        "expected_worker_reused": (
            any(job["agent_name"] == target and not job["new_agent_created"] for job in dispatched)
            if target else None
        ),
        "automatic_shortlist_size": len(automatic_shortlist) if version == "lookup" else None,
        "manual_shortlist_sizes": [len(shortlist) for shortlist in shortlists],
        "expected_worker_in_automatic_shortlist": (
            any(match["name"] == target for match in automatic_shortlist)
            if target and version == "lookup" else None
        ),
        "expected_worker_retrieved": (
            any(match["name"] == target for shortlist in shortlists for match in shortlist)
            if target and shortlists else None
        ),
        "new_workers_created": sum(job["new_agent_created"] for job in dispatched),
        "task_success": (
            None if model_failed else result.success and success
        ),
        "conflicting_drafts": conflicting_drafts,
        "correct_draft_shown": correct_draft_shown,
        "initial_turn_success": result.success,
        "initial_turn_error": redact_error(result.error),
        "replies": replies,
        "reply_times": reply_times,
        "first_correct_reply_seconds": first_correct_reply_seconds,
        "worker_results": [payload for tag, _, payload in entries if tag == "agent_message"],
        "routing": routing,
        "interaction_tools": interaction_tools,
        "gmail_calls": mailbox.calls,
        "unexpected_write_attempts": writes,
        "interaction_input_tokens": (
            sum(interaction_tokens) if all(value is not None for value in interaction_tokens) else None
        ),
        "initial_interaction_input_tokens": interaction_tokens[0] if interaction_tokens else None,
        "execution_input_tokens": (
            sum(execution_tokens) if all(value is not None for value in execution_tokens) else None
        ),
        "model_calls": calls,
        "elapsed_seconds": round(elapsed, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=DEFAULT_BASELINE)
    parser.add_argument("--large-extra-workers", type=int, default=100)
    parser.add_argument("--interaction-model", default="anthropic/claude-sonnet-4.6")
    parser.add_argument("--execution-model", default="anthropic/claude-sonnet-4.6")
    parser.add_argument("--timeout", type=float, default=180, help="Per-run timeout in seconds.")
    parser.add_argument("--output", type=Path, help="Write JSON Lines results to this file.")
    parser.add_argument("--cases-file", type=Path, required=True,
                        help="Replay cases from a prepared routing snapshot.")
    parser.add_argument("--case", action="append", help="Run only this case. Repeat to select several.")
    parser.add_argument(
        "--target-position", choices=("early", "middle", "late"),
        help="Run cases at this position on the large roster.",
    )
    parser.add_argument("--version", choices=("baseline", "lookup"), help="Run only this version.")
    parser.add_argument("--include-small", action="store_true",
                        help="Include the small roster at the selected target position.")
    parser.add_argument("--child", nargs=5, metavar=("CHECKOUT", "VERSION", "EXTRA", "CASE", "POSITION"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    args.cases_file = args.cases_file.resolve()
    cases = {case["id"]: case for case in json.loads(args.cases_file.read_text(encoding="utf-8"))}
    if not cases or any(case.get("kind") != "retrieve" or "mailbox" not in case for case in cases.values()):
        parser.error("--cases-file must contain prepared draft retrieval cases")
    case_ids = list(cases)
    if args.case and any(case_id not in cases for case_id in args.case):
        parser.error(f"Unknown case. Available cases: {', '.join(case_ids)}")

    if args.child:
        checkout, version, extra, case_id, position = args.child
        print(json.dumps(asyncio.run(run_case(Path(checkout), version, int(extra), args, case_id, position))))
        return

    if args.include_small and not args.target_position:
        parser.error("--include-small requires --target-position")
    selected_cases = args.case or case_ids
    key = read_key()
    if not key:
        parser.error("Set OPENROUTER_API_KEY in the environment or the OpenPoke .env file.")
    if args.large_extra_workers < 1 or args.timeout <= 0:
        parser.error("Roster growth and timeout must be positive.")
    env = {**os.environ, "OPENROUTER_API_KEY": key}

    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        baseline = base / "baseline"
        lookup = base / "lookup"
        archive = subprocess.check_output(
            ["git", "archive", "--format=zip", args.baseline_ref, "server"], cwd=ROOT
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            source.extractall(baseline)
        align_baseline_wait(baseline)
        shutil.copytree(
            ROOT / "server", lookup / "server",
            ignore=shutil.ignore_patterns("data", "__pycache__"),
        )

        for case_id in selected_cases:
            for version, checkout in (("baseline", baseline), ("lookup", lookup)):
                if args.version and version != args.version:
                    continue
                roster_sizes = (
                    (args.large_extra_workers,) if args.target_position and not args.include_small
                    else (0, args.large_extra_workers)
                )
                for extra in roster_sizes:
                    positions = (
                        (args.target_position,) if args.target_position
                        else ("early", "middle", "late") if extra
                        else ("default",)
                    )
                    for position in positions:
                        print(f"Running {version} case {case_id} with {extra} extra workers ({position})", file=sys.stderr)
                        process = subprocess.run(
                            [
                                sys.executable, str(Path(__file__).resolve()),
                                "--interaction-model", args.interaction_model,
                                "--execution-model", args.execution_model,
                                "--timeout", str(args.timeout),
                                "--cases-file", str(args.cases_file),
                                "--child", str(checkout), version, str(extra), case_id, position,
                            ],
                            cwd=checkout, env=env, check=True, stdout=subprocess.PIPE, text=True,
                        )
                        record = json.dumps(json.loads(process.stdout))
                        print(record, flush=True)
                        if args.output:
                            with args.output.open("a", encoding="utf-8") as output:
                                output.write(record + "\n")


if __name__ == "__main__":
    main()

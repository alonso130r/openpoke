import unittest

from run_routing_eval import score_draft_replies


class DraftReplyScoringTests(unittest.TestCase):
    def test_correct_draft_followed_by_fabricated_draft_is_flagged(self):
        expected = {
            "recipient_email": "billing@acme.example",
            "subject": "2027 renewal fee waiver",
            "body": "Could you waive the renewal fee as an exception?",
        }
        correct = f'To: {expected["recipient_email"]}\nSubject: {expected["subject"]}\n\n{expected["body"]}'
        fabricated = "To: sarah.lin@acmecorp.com\nSubject: Request for Fee Waiver\n\n> Please waive the $1,200 fee."
        self.assertEqual(score_draft_replies([correct, "Here is the draft."], expected), (True, []))
        returned_body, conflicts = score_draft_replies([correct, fabricated], expected)
        self.assertTrue(returned_body)
        self.assertEqual(conflicts, [{
            "reply_index": 1, "recipient": "sarah.lin@acmecorp.com", "subject": "Request for Fee Waiver",
        }])

    def test_quotes_and_separate_commentary_are_not_draft_conflicts(self):
        expected = {
            "recipient_email": "billing@acme.example",
            "subject": "Renewal",
            "body": "Could you waive the fee?",
        }
        for body in ("> Could you waive the fee?", "“Could you waive the fee?”", "Could you waive the fee?\n\nIt hasn't been sent."):
            with self.subTest(body=body):
                reply = f"To: billing@acme.example\nSubject: Renewal\n\n{body}"
                self.assertEqual(score_draft_replies([reply], expected), (True, []))


class ProvenanceFixtureTests(unittest.TestCase):
    def test_workload_fixtures_give_only_one_worker_the_approval(self):
        import json
        from pathlib import Path

        fixture = Path(__file__).with_name("fixtures") / "workload_approval_setup.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case["id"]):
                workers = case["workers"]
                self.assertEqual(len(workers), 5)
                self.assertEqual([worker for worker in workers if worker.get("approver")], [workers[0]])
                self.assertEqual(len({(worker["recipient_email"], worker["subject"]) for worker in workers}), 1)
                self.assertEqual(len({worker["body"] for worker in workers}), 5)
                self.assertTrue(all("approved" not in worker["body"].lower() for worker in workers))

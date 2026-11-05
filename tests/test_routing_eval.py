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
        fabricated = "To: sarah.lin@acmecorp.com\nSubject: Request for Fee Waiver\n\nPlease waive the $1,200 fee."
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
        for body in ("“Could you waive the fee?”", "Could you waive the fee?\n\nIt hasn't been sent."):
            with self.subTest(body=body):
                reply = f"To: billing@acme.example\nSubject: Renewal\n\n{body}"
                self.assertEqual(score_draft_replies([reply], expected), (True, []))

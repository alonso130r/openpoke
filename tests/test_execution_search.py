import unittest

from server.services.execution.search import search_agents


class ExecutionSearchTests(unittest.TestCase):
    def test_whole_terms_distinct_hits_and_legacy_name_fallback(self):
        records = [
            {"name": "Jaime", "summary": "Drafted a proposal for jaime@example.com."},
            {"name": "Old Jai", "summary": None},
            {"name": "Renewal", "summary": "Jai accepted via jai@example.com on thread-123."},
        ]

        self.assertEqual(
            search_agents("JAI, Jai jai@example.com thread-123 please", records),
            [
                {**records[2], "matched_terms": ["jai", "jai@example.com", "thread-123"]},
                {**records[1], "matched_terms": ["jai"]},
            ],
        )
        for query in ("", "Did they get it?", "thread-12", "vijay@example.com"):
            with self.subTest(query=query):
                self.assertEqual(search_agents(query, records), [])

    def test_ranks_by_hit_count_then_name_and_caps_results(self):
        records = [
            {"name": name, "summary": summary}
            for name, summary in [
                ("Delta", "Renewal"),
                ("Charlie", "Renewal"),
                ("Zulu", "Renewal waiver"),
                ("Alpha", "Renewal"),
            ]
        ]
        matches = search_agents("renewal waiver renewal", records)
        self.assertEqual([match["name"] for match in matches], ["Zulu", "Alpha", "Charlie"])

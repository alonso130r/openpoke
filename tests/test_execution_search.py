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

    def test_ranks_by_hit_count_and_includes_all_ties_at_cutoff(self):
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
        self.assertEqual([match["name"] for match in matches], ["Zulu", "Alpha", "Charlie", "Delta"])

    def test_hyphenated_query_keeps_the_waiver_in_a_crowded_shortlist(self):
        records = [
            {"name": f"Acme renewal archive {index}", "summary": "Reviewed an older Acme renewal."}
            for index in range(100)
        ]
        records.append({"name": "Acme waiver", "summary": "Drafted an Acme renewal fee waiver."})
        match = search_agents("Acme fee-waiver draft", records)[0]
        self.assertEqual(match["name"], "Acme waiver")
        self.assertEqual(match["matched_terms"], ["acme", "fee", "waiver"])

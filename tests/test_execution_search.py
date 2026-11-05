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

    def test_distinctive_terms_filter_generic_matches_before_expanding_ties(self):
        records = [
            {"name": f"Archive {index}", "summary": "Reviewed an Acme renewal."}
            for index in range(20)
        ]
        records.extend(
            {"name": f"Orion {index}", "summary": "Drafted an Orion renewal exception."}
            for index in range(4)
        )
        matches = search_agents("Orion renewal exception", records)
        self.assertEqual([match["name"] for match in matches], [f"Orion {index}" for index in range(4)])
        self.assertEqual(len(search_agents("renewal", records)), 24)

    def test_name_and_summary_supply_distinct_terms_without_double_counting(self):
        record = {
            "name": "Northstar incident customer update Security review",
            "summary": "Recorded Security approval for the Northstar incident update.",
        }
        match = search_agents("Northstar customer update Security approval", [record])[0]
        self.assertEqual(match["matched_terms"], ["approval", "customer", "northstar", "security", "update"])

    def test_rare_term_does_not_exclude_equally_strong_matches(self):
        records = [
            {"name": f"Northstar customer update Security {index}", "summary": None}
            for index in range(6)
        ]
        records.append({"name": "Northstar customer call update", "summary": None})
        matches = search_agents("Northstar customer update Security call", records)
        self.assertEqual(len(matches), 7)

import json
import tempfile
import unittest
from pathlib import Path

from server.services.execution.roster import AgentRoster


class AgentRosterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "roster.json"

    def test_legacy_names_and_optional_summaries_survive_reload(self):
        self.path.write_text(json.dumps(["Jai"]), encoding="utf-8")
        roster = AgentRoster(self.path)

        self.assertEqual(roster.get_agents(), ["Jai"])
        self.assertEqual(roster.get_records(), [{"name": "Jai", "summary": None}])
        roster.add_agent("Vijay")
        self.assertTrue(roster.update_summary("Jai", "Drafted a renewal email."))
        roster.add_agent("Jai")

        self.assertEqual(AgentRoster(self.path).get_records(), [
            {"name": "Jai", "summary": "Drafted a renewal email."},
            {"name": "Vijay", "summary": None},
        ])

    def test_stale_instance_preserves_other_updates_and_cannot_restore_clear(self):
        first = AgentRoster(self.path)
        second = AgentRoster(self.path)
        first.add_agent("Jai")
        second.add_agent("Vijay")
        first.update_summary("Jai", "Drafted a renewal email.")
        second.update_summary("Vijay", "Checked the proposal draft.")

        self.assertEqual(AgentRoster(self.path).get_records(), [
            {"name": "Jai", "summary": "Drafted a renewal email."},
            {"name": "Vijay", "summary": "Checked the proposal draft."},
        ])
        first.clear()
        self.assertFalse(second.update_summary("Vijay", "Finished late."))
        self.assertEqual(AgentRoster(self.path).get_agents(), [])

    def test_creation_rejects_names_that_share_an_execution_log(self):
        roster = AgentRoster(self.path)
        self.assertTrue(roster.add_agent("Jai Renewal"))
        self.assertFalse(roster.add_agent("Jai Renewal"))
        self.assertFalse(roster.add_agent("jai-renewal"))
        self.assertEqual(AgentRoster(self.path).get_agents(), ["Jai Renewal"])

    def test_creation_rejects_an_existing_log_without_a_roster_entry(self):
        (self.path.parent / "jai-renewal.log").write_text("Prior work.", encoding="utf-8")
        roster = AgentRoster(self.path)
        self.assertFalse(roster.add_agent("Jai Renewal"))
        self.assertEqual(roster.get_agents(), [])

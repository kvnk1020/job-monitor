import json
import tempfile
import unittest
from unittest.mock import patch

import job_monitor


class JobMonitorTests(unittest.TestCase):
    def test_matches_only_exact_target_title_phrases(self):
        matching_titles = [
            "UX Designer",
            "Senior Product Designer",
            "Product Designer II",
            "Product Designer, Growth",
            "UI/UX Designer",
            "User Experience Designer",
            "Interaction Designer",
        ]
        non_matching_titles = [
            "Data Center Technician - Quincy, WA",
            "Product Design Manager",
            "Product Designers",
            "Senior UX Design Manager",
        ]

        for title in matching_titles:
            with self.subTest(title=title):
                self.assertTrue(job_monitor.matches_design_role(title))

        for title in non_matching_titles:
            with self.subTest(title=title):
                self.assertFalse(job_monitor.matches_design_role(title))

    def test_load_state_handles_missing_and_empty_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = f"{temp_dir}/state.json"

            with patch.object(job_monitor, "STATE_FILE", state_path):
                self.assertEqual(job_monitor.load_state(), {})

                with open(state_path, "w", encoding="utf-8") as fh:
                    fh.write("")

                self.assertEqual(job_monitor.load_state(), {})

    def test_main_rejects_placeholder_ntfy_topic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            companies_path = f"{temp_dir}/companies.json"
            state_path = f"{temp_dir}/state.json"
            with open(companies_path, "w", encoding="utf-8") as fh:
                json.dump([{"name": "Acme", "type": "greenhouse"}], fh)

            def fake_fetcher(company):
                return [{
                    "id": "job-1",
                    "title": "Product Designer",
                    "url": "https://example.com/job",
                    "location": "Remote",
                }]

            with patch.object(job_monitor, "COMPANIES_FILE", companies_path), \
                 patch.object(job_monitor, "STATE_FILE", state_path), \
                 patch.object(job_monitor, "NTFY_TOPIC", "changeme-to-a-private-topic-name"), \
                 patch.dict(job_monitor.FETCHERS, {"greenhouse": fake_fetcher}, clear=True):
                with self.assertRaisesRegex(ValueError, "NTFY_TOPIC"):
                    job_monitor.main()

    @patch.object(job_monitor.requests, "post", side_effect=job_monitor.requests.Timeout("timed out"))
    def test_notification_timeout_does_not_raise(self, post):
        with patch.object(job_monitor, "NTFY_TOPIC", "test-topic"):
            job_monitor.send_notification("Test title", "Test message")

        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()

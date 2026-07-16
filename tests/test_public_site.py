import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "src/public/main.jsx").read_text()
STYLES = (ROOT / "src/public/styles.css").read_text()
INDEX = (ROOT / "index.html").read_text()


class PublicSiteContractTests(unittest.TestCase):
    def test_homepage_is_the_project_not_a_hardware_lab(self):
        combined = MAIN + INDEX
        self.assertNotIn("Hardware Lab", combined)
        self.assertIn("An independent open-source project", combined)
        self.assertIn("Vivint has not created, sponsored, endorsed, or contributed", combined)

    def test_public_copy_describes_the_integration_path_without_cloud_free_claims(self):
        self.assertIn("Your Vivint system can keep using Vivint's cloud normally", MAIN)
        self.assertIn("Vivint services continue normally", MAIN)
        self.assertNotIn("your data stays local", MAIN.lower())
        self.assertNotIn("everything stays local", MAIN.lower())
        self.assertNotIn("locality lens", MAIN.lower())

    def test_public_navigation_uses_clean_routes(self):
        for route in ("/compatibility", "/architecture", "/updates", "/hardware", "/requests"):
            self.assertIn(f'href="{route}"', MAIN)

    def test_disabled_sms_rollout_copy_is_not_public(self):
        self.assertNotIn("Pending carrier and AWS activation", MAIN)
        self.assertIn("config.contributorSmsEnabled &&", MAIN)

    def test_wordmark_one_uses_the_purple_identity_color(self):
        self.assertIn(".brand span { color: var(--purple); }", STYLES)


if __name__ == "__main__":
    unittest.main()

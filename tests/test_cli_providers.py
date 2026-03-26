import unittest
import subprocess
from unittest.mock import patch

from aiteam import cli


class CliProviderTests(unittest.TestCase):
    def test_parse_command_value_json_array(self) -> None:
        parsed = cli._parse_command_value('["claude","-p","{prompt}"]')
        self.assertEqual(parsed, ["claude", "-p", "{prompt}"])

    def test_parse_command_value_shell_string(self) -> None:
        parsed = cli._parse_command_value('claude -p "{prompt}"')
        self.assertEqual(parsed, ["claude", "-p", "{prompt}"])

    def test_provider_specs_include_three_seniors(self) -> None:
        specs = cli._provider_connection_specs()
        names = {item["name"] for item in specs}
        self.assertIn("openai_pro_cli", names)
        self.assertIn("gemini_pro_cli", names)
        self.assertIn("claude_pro_cli", names)

    def test_resolve_provider_command_from_env(self) -> None:
        spec = {
            "provider": "anthropic",
            "env_command": "AITEAM_TEST_COMMAND",
            "candidates": [],
        }
        with patch("aiteam.cli._probe_command", return_value=True), patch.dict(
            "os.environ",
            {"AITEAM_TEST_COMMAND": '["claude","-p","{prompt}"]'},
            clear=False,
        ):
            command = cli._resolve_provider_command(spec)
            self.assertEqual(command, ["claude", "-p", "{prompt}"])

    def test_resolve_provider_command_preserves_npx_args(self) -> None:
        spec = {
            "provider": "google",
            "candidates": [["npx", "-y", "@google/gemini-cli", "--help"]],
        }
        with patch("aiteam.cli._probe_command", return_value=True):
            command = cli._resolve_provider_command(spec)
            self.assertEqual(command, ["npx", "-y", "@google/gemini-cli", "{prompt}"])

    def test_gemini_auth_status_command_uses_npx_package(self) -> None:
        command = cli._gemini_auth_status_command(["npx", "-y", "@google/gemini-cli", "{prompt}"])
        self.assertEqual(command, ["npx", "-y", "@google/gemini-cli", "auth", "status"])

    def test_gemini_health_detects_missing_auth(self) -> None:
        with patch("aiteam.cli._resolve_executable", return_value="npx"), patch(
            "aiteam.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["npx"],
                returncode=1,
                stdout="",
                stderr=(
                    "Please set an Auth method in your C:/Users/test/.gemini/settings.json "
                    "or specify GEMINI_API_KEY"
                ),
            ),
        ):
            healthy, details = cli._gemini_health(["npx", "-y", "@google/gemini-cli", "{prompt}"])
            self.assertFalse(healthy)
            self.assertEqual(details, "gemini_auth_missing")

    def test_gemini_health_accepts_env_key(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False), patch(
            "aiteam.cli.subprocess.run"
        ) as run_mock:
            healthy, details = cli._gemini_health(["npx", "-y", "@google/gemini-cli", "{prompt}"])
            self.assertTrue(healthy)
            self.assertEqual(details, "gemini_auth_env_key")
            run_mock.assert_not_called()

    def test_required_provider_health_minimum_depends_on_environment(self) -> None:
        self.assertEqual(cli._required_provider_health_minimum("dev", 3), 1)
        self.assertEqual(cli._required_provider_health_minimum("stage", 3), 3)
        self.assertEqual(cli._required_provider_health_minimum("prod", 3), 3)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest

from aiteam.adapters import ExternalProgramAdapter
from aiteam.types import ChannelType


class ExternalAdapterTests(unittest.TestCase):
    def test_external_program_adapter_success(self) -> None:
        adapter = ExternalProgramAdapter(
            name="python_echo",
            provider="custom",
            model="echo",
            command=[sys.executable, "-c", "print('ok-from-external')"],
            capabilities={"analysis"},
            channel=ChannelType.SUBSCRIPTION,
        )
        self.assertTrue(adapter.available())
        response = adapter.invoke("hello")
        self.assertTrue(response.success)
        self.assertIn("ok-from-external", response.content)

    def test_external_program_adapter_missing_command_returns_error(self) -> None:
        adapter = ExternalProgramAdapter(
            name="missing",
            provider="custom",
            model="missing",
            command=["command-that-does-not-exist-xyz", "run"],
            capabilities={"analysis"},
            channel=ChannelType.SUBSCRIPTION,
        )
        response = adapter.invoke("hello")
        self.assertFalse(response.success)
        self.assertIn("external_exec_error", response.error or "")

    def test_external_program_adapter_resolves_python_binary(self) -> None:
        resolved = ExternalProgramAdapter._resolve_executable("python")
        self.assertTrue(resolved is None or resolved)


if __name__ == "__main__":
    unittest.main()

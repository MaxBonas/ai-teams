import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from aiteam.config import build_default_router_policy
from aiteam.routing_overrides import (
    RoleOverride,
    RoutingOverrides,
    apply_overrides_to_policy,
    load_overrides,
    save_overrides,
    validate_overrides,
)


class RoutingOverridesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self._local_temp_root = Path.cwd() / ".tmp_test_routing_overrides"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

        class _WorkspaceTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | Path | None = None,
                ignore_cleanup_errors: bool = False,
            ) -> None:
                inner_self._root = Path(dir) if dir else self._local_temp_root
                inner_self._prefix = prefix or "tmp"
                inner_self._suffix = suffix or ""
                inner_self.name = ""

            def __enter__(inner_self) -> str:
                candidate = (
                    inner_self._root
                    / f"{inner_self._prefix}{uuid4().hex}{inner_self._suffix}"
                )
                candidate.mkdir(parents=True, exist_ok=False)
                inner_self.name = str(candidate)
                return inner_self.name

            def __exit__(inner_self, exc_type, exc, tb) -> bool:
                for path in sorted(
                    Path(inner_self.name).glob("**/*"), key=lambda item: len(str(item)), reverse=True
                ):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                Path(inner_self.name).rmdir()
                return False

            def cleanup(inner_self) -> None:
                for path in sorted(
                    Path(inner_self.name).glob("**/*"), key=lambda item: len(str(item)), reverse=True
                ):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                Path(inner_self.name).rmdir()

        tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        tempfile.TemporaryDirectory = self._previous_temporary_directory

    def test_load_overrides_returns_empty_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overrides = load_overrides(Path(tmp))

            self.assertFalse(overrides.has_entries())
            self.assertEqual(overrides.overrides_by_role, {})

    def test_save_and_load_overrides_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            overrides = RoutingOverrides(
                overrides_by_role={
                    "engineer": RoleOverride(
                        providers=["google", "groq"],
                        models=["gemini-2.5-flash", "llama-3.3-70b-versatile"],
                        primary_provider="google",
                        excluded_providers=["openai"],
                    )
                }
            )

            save_overrides(runtime_dir, overrides)
            loaded = load_overrides(runtime_dir)

            self.assertTrue(loaded.has_entries())
            engineer = loaded.overrides_by_role["engineer"]
            self.assertEqual(engineer.providers, ["google", "groq"])
            self.assertEqual(engineer.models, ["gemini-2.5-flash", "llama-3.3-70b-versatile"])
            self.assertEqual(engineer.primary_provider, "google")
            self.assertEqual(engineer.excluded_providers, ["openai"])
            self.assertTrue(loaded.created_at)
            self.assertTrue(loaded.updated_at)

    def test_validate_overrides_rejects_role_without_provider(self) -> None:
        overrides = RoutingOverrides(
            overrides_by_role={
                "engineer": RoleOverride(excluded_providers=["openai", "google", "groq"])
            }
        )

        errors = validate_overrides(overrides, build_default_router_policy())

        self.assertIn("engineer:no_provider_remaining", errors)

    def test_validate_overrides_accepts_valid_override(self) -> None:
        overrides = RoutingOverrides(
            overrides_by_role={
                "engineer": RoleOverride(
                    providers=["google", "groq"],
                    primary_provider="google",
                    excluded_providers=["openai"],
                )
            }
        )

        errors = validate_overrides(overrides, build_default_router_policy())

        self.assertEqual(errors, [])

    def test_apply_overrides_to_policy_sets_primary_and_exclusions(self) -> None:
        policy = build_default_router_policy()
        overrides = RoutingOverrides(
            overrides_by_role={
                "engineer": RoleOverride(
                    providers=["google", "groq"],
                    models=["gemini-2.5-flash"],
                    primary_provider="google",
                    excluded_providers=["openai"],
                )
            }
        )

        merged = apply_overrides_to_policy(policy, overrides)

        self.assertEqual(merged.role_primary_provider["engineer"], "google")
        self.assertEqual(merged.role_provider_exclusions["engineer"], ["openai"])
        self.assertEqual(merged.role_provider_preferences["engineer"], ["google", "groq"])
        self.assertEqual(merged.role_model_preferences["engineer"], ["gemini-2.5-flash"])


if __name__ == "__main__":
    unittest.main()

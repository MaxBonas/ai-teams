import unittest

from aiteam.tool_specialists import (
    SpecialistReport,
    build_tool_specialist_metadata,
    infer_tool_specialist,
    parse_specialist_report,
    replacement_specialists_from_metadata,
    specialist_system_prompt_block,
)
from aiteam.types import Role


class ToolSpecialistsTests(unittest.TestCase):
    def test_infer_repo_scout_for_scout_repo_read(self) -> None:
        specialist = infer_tool_specialist(
            role=Role.SCOUT,
            required_capabilities=["analysis", "documentation"],
        )
        self.assertEqual(specialist, "repo_scout")

    def test_infer_browser_operator_for_browser_capabilities(self) -> None:
        specialist = infer_tool_specialist(
            role=Role.QA,
            required_capabilities=["browser_testing"],
        )
        self.assertEqual(specialist, "browser_operator")

    def test_infer_reviewer_prefers_lsp_navigator_for_review_scope(self) -> None:
        specialist = infer_tool_specialist(
            role=Role.REVIEWER,
            required_capabilities=["review", "repo_read", "reasoning"],
        )
        self.assertEqual(specialist, "lsp_navigator")

    def test_infer_qa_prefers_test_runner_when_test_and_browser_capabilities_coexist(self) -> None:
        specialist = infer_tool_specialist(
            role=Role.QA,
            required_capabilities=["browser_testing", "test_execute"],
        )
        self.assertEqual(specialist, "test_runner")

    def test_build_metadata_marks_operate_tools_only_scope(self) -> None:
        metadata = build_tool_specialist_metadata(
            specialist="browser_operator",
            required_capabilities=["browser_testing"],
            reason="reproducir issue de UI",
        )
        self.assertEqual(metadata["tool_specialist"], "browser_operator")
        self.assertEqual(
            metadata["tool_specialist_decision_scope"],
            "operate_tools_and_report_only",
        )
        self.assertTrue(metadata["tool_specialist_economic_routing"])
        self.assertIn("browser_test", metadata["tool_specialist_preferred_capabilities"])

    def test_infer_skill_worker_and_lsp_navigator_from_targets(self) -> None:
        self.assertEqual(
            infer_tool_specialist(
                role=Role.SCOUT,
                required_capabilities=[],
                metadata={"skill_targets": ["playwright"]},
            ),
            "skill_worker",
        )
        self.assertEqual(
            infer_tool_specialist(
                role=Role.RESEARCHER,
                required_capabilities=[],
                metadata={"lsp_targets": ["symbols"]},
            ),
            "lsp_navigator",
        )

    def test_infer_specialist_respects_tool_rewiring_preference(self) -> None:
        self.assertEqual(
            infer_tool_specialist(
                role=Role.ENGINEER,
                required_capabilities=["external_mcp"],
                metadata={"tool_rewiring_preferred_specialist": "skill_worker"},
            ),
            "skill_worker",
        )

    def test_infer_specialist_supports_context_curator_request(self) -> None:
        self.assertEqual(
            infer_tool_specialist(
                role=Role.SCOUT,
                required_capabilities=["repo_read"],
                metadata={"context_curator_requested": True},
            ),
            "context_curator",
        )

    def test_replacement_specialists_from_metadata_maps_known_replacements(self) -> None:
        specialists = replacement_specialists_from_metadata(
            {
                "tool_rewiring_candidates": [
                    "semgrep_security_skill",
                    "playwright",
                    "pytest_runner",
                ]
            }
        )
        self.assertIn("skill_worker", specialists)
        self.assertIn("browser_operator", specialists)
        self.assertIn("test_runner", specialists)

    def test_build_metadata_derives_capabilities_from_targets(self) -> None:
        metadata = build_tool_specialist_metadata(
            specialist="skill_worker",
            required_capabilities=[],
            skill_targets=["playwright"],
            lsp_targets=["impact"],
        )
        preferred = metadata["tool_specialist_preferred_capabilities"]
        self.assertIn("skill_run", preferred)
        self.assertIn("lsp_references", preferred)

    def test_specialist_system_prompt_block_mentions_non_decision_scope(self) -> None:
        block = specialist_system_prompt_block(
            build_tool_specialist_metadata(
                specialist="repo_scout",
                required_capabilities=["analysis"],
                reason="inspeccionar repo",
                skill_targets=["context7_research_skill"],
                lsp_targets=["symbols"],
            )
        )
        self.assertIn("Repo Scout", block)
        self.assertIn("No arbitres producto", block)
        self.assertIn("summary", block)
        self.assertIn("confidence", block)
        self.assertIn("Skills objetivo", block)
        self.assertIn("Objetivos LSP", block)

    def test_specialist_system_prompt_block_mentions_context_curator(self) -> None:
        block = specialist_system_prompt_block(
            build_tool_specialist_metadata(
                specialist="context_curator",
                required_capabilities=["repo_read"],
                reason="compactacion semantica durante la corrida",
            )
        )
        self.assertIn("Context Curator", block)
        self.assertIn("memoria util por capas", block)

    def test_parse_specialist_report_well_formed_json(self) -> None:
        report = parse_specialist_report(
            """{"summary":"Hallazgo claro","evidence":["stacktrace","screenshot"],"artifacts":["runtime/log.txt"],"risks":["regresion"],"recommendation":"reintentar con otro selector","confidence":0.82}""",
            specialist="browser_operator",
            provider="openai",
            model="gpt-4o-mini",
            toolset_used=["browser", "mcp"],
            tokens_used=123,
        )
        self.assertEqual(report.specialist, "browser_operator")
        self.assertEqual(report.summary, "Hallazgo claro")
        self.assertEqual(report.evidence, ["stacktrace", "screenshot"])
        self.assertEqual(report.artifacts, ["runtime/log.txt"])
        self.assertEqual(report.risks, ["regresion"])
        self.assertEqual(report.recommendation, "reintentar con otro selector")
        self.assertEqual(report.provider, "openai")
        self.assertEqual(report.model, "gpt-4o-mini")
        self.assertEqual(report.tokens_used, 123)
        self.assertEqual(report.validation_status, "valid")
        self.assertEqual(report.validation_errors, [])

    def test_parse_specialist_report_malformed_text_falls_back_to_summary(self) -> None:
        report = parse_specialist_report(
            "texto libre sin estructura pero con observaciones utiles",
            specialist="repo_scout",
            provider="anthropic",
            model="haiku",
            toolset_used=["repo"],
            tokens_used=50,
        )
        self.assertEqual(report.specialist, "repo_scout")
        self.assertEqual(report.summary, "texto libre sin estructura pero con observaciones utiles")
        self.assertEqual(report.evidence, [])
        self.assertEqual(report.artifacts, [])
        self.assertEqual(report.risks, [])
        self.assertEqual(report.recommendation, "")
        self.assertEqual(report.toolset_used, ["repo"])
        self.assertEqual(report.validation_status, "valid")

    def test_parse_specialist_report_empty_is_safe(self) -> None:
        report = parse_specialist_report(
            "",
            specialist="skill_worker",
            provider="openai",
            model="mini",
        )
        self.assertEqual(report.summary, "")
        self.assertEqual(report.evidence, [])
        self.assertEqual(report.report_version, "specialist_report_v1")
        self.assertEqual(report.validation_status, "invalid")
        self.assertIn("missing_summary", report.validation_errors)

    def test_parse_specialist_report_normalizes_duplicates_and_ranges(self) -> None:
        report = parse_specialist_report(
            """{"summary":"UI rota","evidence":["stacktrace","stacktrace"],"artifacts":["runtime/log.txt","runtime/log.txt"],"risks":["regresion","Regresion"],"confidence":1.7,"tokens_used":-12}""",
            specialist="browser_operator",
            toolset_used=["browser", "browser"],
        )
        self.assertEqual(report.evidence, ["stacktrace"])
        self.assertEqual(report.artifacts, ["runtime/log.txt"])
        self.assertEqual(report.risks, ["regresion"])
        self.assertEqual(report.toolset_used, ["browser"])
        self.assertEqual(report.confidence, 1.0)
        self.assertEqual(report.tokens_used, 0)

    def test_specialist_report_from_metadata_roundtrip_is_canonical(self) -> None:
        report = SpecialistReport.from_metadata(
            {
                "specialist": "repo_scout",
                "summary": "hallazgo util",
                "evidence": ["a", "a"],
                "toolset_used": ["repo", "repo"],
                "confidence": 0.5,
                "tokens_used": 42,
            }
        )
        self.assertEqual(report.specialist, "repo_scout")
        self.assertEqual(report.evidence, ["a"])
        self.assertEqual(report.toolset_used, ["repo"])
        self.assertEqual(report.validation_status, "valid")


if __name__ == "__main__":
    unittest.main()

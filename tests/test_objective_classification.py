from __future__ import annotations

import json

import pytest

from aiteam.objective_classification import (
    MIXED,
    NON_CODE,
    OPERATIONS,
    SOFTWARE,
    classify_objective,
    objective_kind_from_issue,
)


def test_cleaning_company_needs_study_is_non_code() -> None:
    result = classify_objective(
        "Estudio para una empresa de limpieza",
        "Crear formularios para analizar sus necesidades, operaciones y clientes.",
    )

    assert result.kind == NON_CODE
    assert result.source == "deterministic_signals"
    assert any("formularios" in reason for reason in result.reasons)


def test_software_and_mixed_objectives_remain_distinct() -> None:
    software = classify_objective(
        "Construir aplicación web",
        "Implementar frontend React, API y base de datos.",
    )
    mixed = classify_objective(
        "Analizar necesidades y construir aplicación web",
        "Preparar formularios para el estudio e implementar frontend y API.",
    )

    assert software.kind == SOFTWARE
    assert mixed.kind == MIXED


def test_operations_document_is_not_misclassified_as_software() -> None:
    operations = classify_objective(
        "Manual operativo",
        "Definir procedimientos, checklist, responsables y protocolo de escalado.",
    )

    assert operations.kind == OPERATIONS


def test_owner_explicit_kind_wins_and_unknown_fails_closed() -> None:
    explicit = classify_objective(
        "Formulario",
        "Texto ambiguo",
        explicit_kind=NON_CODE,
    )
    assert explicit.kind == NON_CODE
    assert explicit.source == "owner_explicit"

    with pytest.raises(ValueError, match="unknown objective kind"):
        classify_objective("x", explicit_kind="anything")


def test_persisted_classification_is_authoritative() -> None:
    classification = classify_objective(
        "Estudio de empresa",
        "Cuestionarios y entrevistas",
    )
    issue = {
        "title": "Implementar API",
        "description": "Código Python",
        "metadata_json": json.dumps(
            {"objective_classification": classification.to_metadata()}
        ),
    }

    assert objective_kind_from_issue(issue) == NON_CODE

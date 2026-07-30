from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aiteam.db.guided_setup import (
    GuidedSetupConflict,
    create_or_resume_setup,
    get_latest_project_preflight_receipt,
    get_project_commit_receipt,
    get_project_preflight_receipt_for_plan,
    get_setup,
    record_project_commit_receipt,
    record_project_preflight_receipt,
    record_setup_preparation,
    reset_setup,
    resolve_project_fixture_evidence,
    setup_contract,
    transition_setup_step,
)
from aiteam.ecosystem_registry import detect_project_ecosystems
from aiteam.guided_setup_adapter_contract_probe import (
    run_exact_adapter_contract_probe,
)
from aiteam.guided_setup_coverage import (
    ADVISORY_ROLES,
    PROFILE_REQUIREMENTS,
    build_guided_setup_coverage,
)
from aiteam.guided_setup_needs import (
    build_needs_submission,
    needs_questionnaire,
)
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_project_commit import (
    materialize_project_proposal,
    rollback_materialized_project,
)
from aiteam.guided_setup_project_preflight import (
    PATH_OBSERVATION_VERSION,
    build_project_preflight,
)
from aiteam.guided_setup_project_preflight_execution import (
    build_project_preflight_execution_plan,
)
from aiteam.guided_setup_project_preflight_executor import (
    execute_project_preflight_plan,
)
from aiteam.guided_setup_project_proposal import (
    build_project_team_proposal,
)
from aiteam.guided_setup_provider_evidence import (
    build_canonical_provider_evidence,
)
from aiteam.guided_setup_provider_guidance import build_provider_guidance
from aiteam.guided_setup_recommendations import (
    build_progressive_recommendations,
)
from aiteam.machine_doctor import build_machine_inventory
from aiteam.model_catalog_service import get_current_model_catalog
from aiteam.model_selection_context import contextual_model_selection
from aiteam.user_config import load_adapter_profiles, user_config_dir
from api.utils import (
    _require_api_auth_request,
    _sanitize_project_name,
    get_configured_projects_root,
    set_current_workspace,
)

router = APIRouter(prefix="/api/guided-setup", tags=["guided-setup"])
SetupScope = Literal[
    "machine_onboarding",
    "project_setup",
    "installation_repair",
]


class CreateSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: SetupScope
    subject_key: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransitionStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["in_progress", "blocked", "skipped", "passed"]
    expected_revision: int = Field(ge=1)
    response: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    blocker_code: str | None = Field(default=None, max_length=255)
    skip_reason: str | None = Field(default=None, max_length=1000)


class ResetSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    confirm: bool


class NeedsAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["machine_onboarding", "project_setup"]
    answers: dict[str, Any] = Field(default_factory=dict)


class PreparationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    selected_api_profile_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
    )


class CoverageRunRequest(PreparationRunRequest):
    pass


class ProjectProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    selected_api_profile_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
    )
    requested_profile: Literal[
        "solo_lead",
        "lead_quorum",
        "full_team",
    ] | None = None
    instructions: str = Field(default="", max_length=20_000)
    overrides_by_agent_id: dict[str, str] = Field(default_factory=dict)


class ProjectCommitRequest(ProjectProposalRequest):
    proposal_hash: str = Field(min_length=64, max_length=64)
    confirm: bool


class ProjectPreflightRequest(ProjectProposalRequest):
    proposal_hash: str = Field(min_length=64, max_length=64)
    fixture_evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=5,
    )


class ProjectPreflightExecutionRequest(ProjectPreflightRequest):
    preflight_hash: str = Field(min_length=64, max_length=64)
    execution_plan_hash: str = Field(min_length=64, max_length=64)
    confirm_local_fixture: bool = False
    confirm_remote_probe: bool = False
    acknowledge_possible_quota: bool = False


@router.get("/contract/{scope}")
async def get_guided_setup_contract(scope: SetupScope, request: Request):
    _require_api_auth_request(request)
    return {"success": True, "contract": setup_contract(scope)}


@router.get("/needs-contract/{scope}")
async def get_guided_setup_needs_contract(
    scope: Literal["machine_onboarding", "project_setup"],
    request: Request,
):
    _require_api_auth_request(request)
    return {"success": True, "questionnaire": needs_questionnaire(scope)}


@router.post("/needs-assessment")
async def post_guided_setup_needs_assessment(
    body: NeedsAssessmentRequest,
    request: Request,
):
    _require_api_auth_request(request)
    try:
        submission = build_needs_submission(body.scope, body.answers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "submission": submission}


@router.post("/sessions")
async def post_guided_setup_session(
    body: CreateSetupRequest,
    request: Request,
):
    _require_api_auth_request(request)
    try:
        session = create_or_resume_setup(
            _db(request),
            scope=body.scope,
            subject_key=body.subject_key,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "session": session}


@router.get("/sessions/{session_id}")
async def get_guided_setup_session(session_id: str, request: Request):
    _require_api_auth_request(request)
    try:
        session = get_setup(_db(request), session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return {"success": True, "session": session}


@router.post("/sessions/{session_id}/preparation")
def post_guided_setup_preparation(
    session_id: str,
    body: PreparationRunRequest,
    request: Request,
):
    _require_api_auth_request(request)
    try:
        context = _preparation_context(
            request,
            session_id,
            expected_revision=body.expected_revision,
            selected_api_profile_ids=body.selected_api_profile_ids,
        )
        plan = context["plan"]
        guidance = build_provider_guidance(plan)
        persisted = record_setup_preparation(
            _db(request),
            session_id,
            expected_revision=body.expected_revision,
            plan=plan,
            inventory=context["inventory"],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "plan": plan,
        "guidance": guidance,
        "canonical_evidence": context["canonical_evidence"],
        "receipt": persisted["receipt"],
        "session": persisted["session"],
    }


@router.post("/sessions/{session_id}/coverage")
def post_guided_setup_coverage(
    session_id: str,
    body: CoverageRunRequest,
    request: Request,
):
    """Project canonical role coverage without persisting or selecting defaults."""
    _require_api_auth_request(request)
    try:
        context = _preparation_context(
            request,
            session_id,
            expected_revision=body.expected_revision,
            selected_api_profile_ids=body.selected_api_profile_ids,
        )
        needs = context["needs"]
        answers = needs["answers"]
        recommended_profile = str(
            needs["assessment"]["recommended_run_profile"]
        )
        required_capabilities = (
            ("external_mcp",)
            if answers.get("external_tools") == "required"
            else ()
        )
        read_model = get_current_model_catalog(db_paths=())
        required_roles = sorted({
            str(requirement["role"])
            for requirements in PROFILE_REQUIREMENTS.values()
            for requirement in requirements
        } | set(ADVISORY_ROLES))
        selections = {
            role: contextual_model_selection(
                _db(request),
                role=role,
                run_profile=recommended_profile,
                criticality=str(answers.get("criticality") or "medium"),
                data_class=str(answers.get("data_sensitivity") or "unknown"),
                required_capabilities=required_capabilities,
                profiles=context["profiles"],
                read_model=read_model,
            )
            for role in required_roles
        }
        ready_profile_ids = {
            str(row["id"])
            for row in context["plan"]["adapters"]
            if row.get("state") == "ready"
        }
        coverage = build_guided_setup_coverage(
            selections,
            ready_profile_ids=ready_profile_ids,
            recommended_profile=recommended_profile,
        )
        recommendations = build_progressive_recommendations(
            coverage,
            context["plan"],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "coverage": coverage,
        "recommendations": recommendations,
        "preparation": {
            "schema_version": context["plan"]["schema_version"],
            "ready": context["plan"]["summary"]["ready"],
            "blockers": context["plan"]["summary"]["blockers"],
            "ready_adapter_ids": sorted(ready_profile_ids),
        },
        "selection_context": {
            "source": "contextual_model_selection",
            "catalog_content_hash": read_model.get("content_hash"),
            "run_profile": recommended_profile,
            "criticality": str(answers.get("criticality") or "medium"),
            "data_class": str(answers.get("data_sensitivity") or "unknown"),
            "required_capabilities": list(required_capabilities),
        },
        "mutation_policy": {
            "defaults_changed": False,
            "project_created": False,
            "preparation_persisted": False,
        },
    }


@router.post("/sessions/{session_id}/project-proposal")
def post_guided_setup_project_proposal(
    session_id: str,
    body: ProjectProposalRequest,
    request: Request,
):
    """Build a server-authoritative project/team preview without mutations."""
    _require_api_auth_request(request)
    try:
        built = _project_proposal_context(session_id, body, request)
        proposal = built["proposal"]
        coverage = built["coverage"]
        context = built["context"]
        ready_profile_ids = built["ready_profile_ids"]
        read_model = built["read_model"]
        selected_profile = built["selected_profile"]
        answers = built["needs"]["answers"]
        required_capabilities = built["required_capabilities"]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "proposal": proposal,
        "coverage": coverage,
        "preparation": {
            "schema_version": context["plan"]["schema_version"],
            "ready": context["plan"]["summary"]["ready"],
            "blockers": context["plan"]["summary"]["blockers"],
            "ready_adapter_ids": sorted(ready_profile_ids),
        },
        "selection_context": {
            "source": "contextual_model_selection",
            "catalog_content_hash": read_model.get("content_hash"),
            "run_profile": selected_profile,
            "criticality": str(answers.get("criticality") or "medium"),
            "data_class": str(answers.get("data_sensitivity") or "unknown"),
            "required_capabilities": list(required_capabilities),
        },
        "mutation_policy": {
            "filesystem_mutated": False,
            "database_mutated": False,
            "project_created": False,
            "agents_created": False,
            "wakeups_created": False,
        },
    }


@router.post("/sessions/{session_id}/project-commit")
def post_guided_setup_project_commit(
    session_id: str,
    body: ProjectCommitRequest,
    request: Request,
):
    """Revalidate and atomically materialize the exact sealed proposal."""
    _require_api_auth_request(request)
    result: dict[str, Any] | None = None
    try:
        session = get_setup(_db(request), session_id)
        if int(session["revision"]) != int(body.expected_revision):
            raise GuidedSetupConflict("guided_setup_revision_conflict")
        existing = get_project_commit_receipt(_db(request), session_id)
        if existing is not None:
            if existing["proposal_hash"] != body.proposal_hash:
                raise GuidedSetupConflict(
                    "guided_setup_project_already_committed"
                )
            existing_db = Path(str(existing["result"].get("database") or ""))
            if not existing_db.is_file():
                raise GuidedSetupConflict(
                    "guided_setup_project_commit_receipt_stale"
                )
            return {
                "success": True,
                "idempotent_replay": True,
                "result": existing["result"],
                "receipt": existing,
            }
        if body.confirm is not True:
            raise ValueError("guided_setup_project_commit_confirmation_required")
        built = _project_proposal_context(session_id, body, request)
        proposal = built["proposal"]
        if proposal["proposal_hash"] != body.proposal_hash:
            raise GuidedSetupConflict("guided_setup_project_proposal_stale")
        preflight_receipt = get_latest_project_preflight_receipt(
            _db(request),
            session_id,
        )
        if preflight_receipt is None:
            raise GuidedSetupConflict(
                "guided_setup_project_preflight_receipt_required"
            )
        if (
            preflight_receipt["proposal_hash"] != body.proposal_hash
            or preflight_receipt["status"] != "go"
        ):
            raise GuidedSetupConflict(
                "guided_setup_project_preflight_not_go"
            )
        fixture_evidence = resolve_project_fixture_evidence(
            _db(request),
            session_id,
            preflight_receipt["fixture_evidence_refs"],
        )
        current_preflight = build_project_preflight(
            built["needs"],
            proposal,
            built["context"]["plan"],
            built["context"]["inventory"],
            _observe_project_path(built["identity"]),
            fixture_evidence=fixture_evidence,
        )
        if (
            current_preflight["preflight_hash"]
            != preflight_receipt["preflight_hash"]
            or current_preflight != preflight_receipt["preflight"]
        ):
            raise GuidedSetupConflict(
                "guided_setup_project_preflight_receipt_stale"
            )
        result = materialize_project_proposal(
            proposal,
            profiles=built["context"]["profiles"],
            schema_path=(
                Path(__file__).resolve().parents[2]
                / "aiteam"
                / "db"
                / "schema.sql"
            ),
        )
        try:
            receipt = record_project_commit_receipt(
                _db(request),
                session_id,
                proposal_hash=body.proposal_hash,
                project_target=result["workspace"],
                result=result,
            )
        except Exception:
            rollback_materialized_project(result)
            raise
        set_current_workspace(Path(result["workspace"]), persist=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "idempotent_replay": False,
        "result": result,
        "receipt": receipt,
    }


@router.post("/sessions/{session_id}/project-preflight")
def post_guided_setup_project_preflight(
    session_id: str,
    body: ProjectPreflightRequest,
    request: Request,
):
    """Recompose a read-only preflight from server-observed evidence."""
    _require_api_auth_request(request)
    try:
        built = _project_preflight_context(session_id, body, request)
        preflight = built["preflight"]
        execution_plan = built["execution_plan"]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "preflight": preflight,
        "execution_plan": execution_plan,
        "server_evidence": {
            "proposal_source": "recomposed_from_session",
            "path_source": "server_filesystem_observation",
            "inventory_source": "machine_doctor",
            "fixture_evidence_refs": list(body.fixture_evidence_refs),
            "scope_semantics": {
                "preflight_scope": "pure_composition_only",
                "doctor_scope": "read_only_runtime_path_port_observation",
            },
        },
        "mutation_policy": {
            "read_only_observation": True,
            "filesystem_mutated": False,
            "database_mutated": False,
            "tests_executed": False,
            "remote_probes_executed": False,
            "inference_attempted": False,
            "quota_consumed": False,
            "version_commands_may_execute": True,
        },
    }


@router.post("/sessions/{session_id}/project-preflight-execute")
def post_guided_setup_project_preflight_execute(
    session_id: str,
    body: ProjectPreflightExecutionRequest,
    request: Request,
):
    """Execute once and durably seal the resulting project authorization."""
    _require_api_auth_request(request)
    try:
        built = _project_preflight_context(session_id, body, request)
        preflight = built["preflight"]
        execution_plan = built["execution_plan"]
        if preflight["preflight_hash"] != body.preflight_hash:
            raise GuidedSetupConflict("guided_setup_project_preflight_stale")
        if execution_plan["plan_hash"] != body.execution_plan_hash:
            raise GuidedSetupConflict(
                "guided_setup_project_preflight_execution_plan_stale"
            )

        def remote_runner(
            profile_id: str,
            model_id: str,
            timeout_seconds: int,
        ) -> dict[str, Any]:
            return run_exact_adapter_contract_probe(
                profile_id,
                model_id,
                timeout_seconds,
                consent_granted=True,
                quota_acknowledged=True,
                profiles=built["proposal_context"]["context"]["profiles"],
            )

        durable = get_project_preflight_receipt_for_plan(
            _db(request),
            session_id,
            body.execution_plan_hash,
        )
        idempotent_replay = bool(
            durable is not None
            and durable["execution_plan_hash"] == body.execution_plan_hash
            and durable["proposal_hash"] == body.proposal_hash
        )
        if idempotent_replay:
            receipt = durable["execution_receipt"]
            post_execution_preflight = durable["preflight"]
        else:
            receipt = execute_project_preflight_plan(
                execution_plan,
                plan_hash=body.execution_plan_hash,
                confirm_local_fixture=body.confirm_local_fixture,
                confirm_remote_probe=body.confirm_remote_probe,
                acknowledge_possible_quota=body.acknowledge_possible_quota,
                remote_probe_runner=remote_runner,
            )
            post_execution_preflight = build_project_preflight(
                built["proposal_context"]["needs"],
                built["proposal_context"]["proposal"],
                built["proposal_context"]["context"]["plan"],
                built["proposal_context"]["context"]["inventory"],
                built["path_observation"],
                fixture_evidence=receipt["fixture_evidence"],
            )
            durable = record_project_preflight_receipt(
                _db(request),
                session_id,
                preflight=post_execution_preflight,
                execution_plan=execution_plan,
                execution_receipt=receipt,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "receipt": receipt,
        "post_execution_preflight": post_execution_preflight,
        "persistence": {
            "persisted": True,
            "idempotent_replay": idempotent_replay,
            "durable_receipt": durable,
            "required_before_commit": False,
        },
    }


@router.patch("/sessions/{session_id}/steps/{step_key}")
async def patch_guided_setup_step(
    session_id: str,
    step_key: str,
    body: TransitionStepRequest,
    request: Request,
):
    _require_api_auth_request(request)
    try:
        session = transition_setup_step(
            _db(request),
            session_id,
            step_key,
            status=body.status,
            expected_revision=body.expected_revision,
            response=body.response,
            evidence=body.evidence,
            blocker_code=body.blocker_code,
            skip_reason=body.skip_reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "session": session}


@router.post("/sessions/{session_id}/reset")
async def post_guided_setup_reset(
    session_id: str,
    body: ResetSetupRequest,
    request: Request,
):
    _require_api_auth_request(request)
    try:
        session = reset_setup(
            _db(request),
            session_id,
            expected_revision=body.expected_revision,
            confirm=body.confirm,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except GuidedSetupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "session": session}


def _db(_request: Request):
    return user_config_dir() / "guided_setup.db"


def _project_proposal_context(
    session_id: str,
    body: ProjectProposalRequest,
    request: Request,
) -> dict[str, Any]:
    """Rebuild every mutable input used by preview and commit."""
    session = get_setup(_db(request), session_id)
    if int(session["revision"]) != int(body.expected_revision):
        raise GuidedSetupConflict("guided_setup_revision_conflict")
    if session["scope"] != "project_setup":
        raise ValueError("guided_setup_project_scope_not_supported")
    if len(body.overrides_by_agent_id) > 10:
        raise ValueError("guided_setup_project_overrides_too_many")
    identity_step = next(
        row for row in session["steps"] if row["key"] == "project_identity"
    )
    objective_step = next(
        row for row in session["steps"] if row["key"] == "objective_profile"
    )
    if (
        identity_step["status"] != "passed"
        or objective_step["status"] != "passed"
    ):
        raise GuidedSetupConflict("guided_setup_dependency_not_satisfied")
    needs = objective_step["response"]
    selected_profile = str(
        body.requested_profile
        or needs["assessment"]["recommended_run_profile"]
    )
    identity = _resolve_project_identity(identity_step["response"])
    ecosystems = detect_project_ecosystems(Path(identity["target"]))
    context = _preparation_bundle(
        needs,
        selected_api_profile_ids=body.selected_api_profile_ids,
        inventory_root=Path(identity["target"]),
    )
    answers = needs["answers"]
    required_capabilities = (
        ("external_mcp",)
        if answers.get("external_tools") == "required"
        else ()
    )
    read_model = get_current_model_catalog(db_paths=())
    required_roles = sorted({
        str(requirement["role"])
        for requirements in PROFILE_REQUIREMENTS.values()
        for requirement in requirements
    } | set(ADVISORY_ROLES))
    selections = {
        role: contextual_model_selection(
            _db(request),
            role=role,
            run_profile=selected_profile,
            criticality=str(answers.get("criticality") or "medium"),
            data_class=str(answers.get("data_sensitivity") or "unknown"),
            required_capabilities=required_capabilities,
            profiles=context["profiles"],
            read_model=read_model,
        )
        for role in required_roles
    }
    ready_profile_ids = {
        str(row["id"])
        for row in context["plan"]["adapters"]
        if row.get("state") == "ready"
    }
    coverage = build_guided_setup_coverage(
        selections,
        ready_profile_ids=ready_profile_ids,
        recommended_profile=selected_profile,
    )
    proposal = build_project_team_proposal(
        needs,
        identity,
        ecosystems,
        coverage,
        requested_profile=body.requested_profile,
        instructions=body.instructions,
        overrides_by_agent_id=body.overrides_by_agent_id,
    )
    return {
        "session": session,
        "needs": needs,
        "identity": identity,
        "selected_profile": selected_profile,
        "required_capabilities": required_capabilities,
        "read_model": read_model,
        "context": context,
        "ready_profile_ids": ready_profile_ids,
        "coverage": coverage,
        "proposal": proposal,
    }


def _preparation_context(
    request: Request,
    session_id: str,
    *,
    expected_revision: int,
    selected_api_profile_ids: list[str],
) -> dict[str, Any]:
    session = get_setup(_db(request), session_id)
    if int(session["revision"]) != int(expected_revision):
        raise GuidedSetupConflict("guided_setup_revision_conflict")
    if session["scope"] != "machine_onboarding":
        raise ValueError("guided_setup_preparation_scope_not_supported")
    needs_step = next(
        row for row in session["steps"] if row["key"] == "needs_profile"
    )
    if needs_step["status"] != "passed":
        raise GuidedSetupConflict("guided_setup_dependency_not_satisfied")
    context = _preparation_bundle(
        needs_step["response"],
        selected_api_profile_ids=selected_api_profile_ids,
    )
    return {
        "session": session,
        "needs": needs_step["response"],
        **context,
    }


def _preparation_bundle(
    needs: dict[str, Any],
    *,
    selected_api_profile_ids: list[str],
    inventory_root: Path | None = None,
) -> dict[str, Any]:
    profiles = load_adapter_profiles()
    profiles_by_id = {str(row.get("id") or ""): row for row in profiles}
    if len(selected_api_profile_ids) != len(set(selected_api_profile_ids)):
        raise ValueError("guided_setup_preparation_api_profile_duplicate")
    selected_api_profiles = []
    for profile_id in selected_api_profile_ids:
        profile = profiles_by_id.get(str(profile_id))
        if profile is None or str(profile.get("channel") or "") != "api":
            raise ValueError("guided_setup_preparation_api_profile_invalid")
        selected_api_profiles.append(profile)
    inventory = build_machine_inventory(
        root=inventory_root or Path(__file__).resolve().parents[2],
        adapter_profiles=profiles,
    )
    initial_plan = build_preparation_plan(
        needs,
        inventory,
        selected_api_profiles=selected_api_profiles,
    )
    canonical_evidence = build_canonical_provider_evidence(
        initial_plan,
        inventory,
        profiles,
    )
    plan = build_preparation_plan(
        needs,
        inventory,
        provider_evidence=canonical_evidence["stage_evidence"],
        selected_api_profiles=selected_api_profiles,
    )
    return {
        "profiles": profiles,
        "inventory": inventory,
        "canonical_evidence": canonical_evidence,
        "plan": plan,
    }


def _resolve_project_identity(intent: dict[str, Any]) -> dict[str, Any]:
    projects_root = get_configured_projects_root().resolve()
    mode = str(intent.get("mode") or "")
    name = _sanitize_project_name(str(intent.get("name") or ""))
    raw_path = str(intent.get("path") or "").strip()
    if mode == "create" and not raw_path:
        target = (projects_root / name).resolve()
    else:
        requested = Path(raw_path)
        target = (
            requested.resolve()
            if requested.is_absolute()
            else (projects_root / requested).resolve()
        )
    if target == projects_root or projects_root not in target.parents:
        raise ValueError("guided_setup_project_path_outside_projects_root")
    return {
        "mode": mode,
        "name": name,
        "target": str(target),
        "target_exists": target.exists(),
        "target_is_dir": target.is_dir(),
    }


def _observe_project_path(identity: dict[str, Any]) -> dict[str, Any]:
    target = Path(str(identity.get("target") or "")).resolve()
    projects_root = get_configured_projects_root().resolve()
    confined = target != projects_root and projects_root in target.parents
    parent = target.parent
    return {
        "schema_version": PATH_OBSERVATION_VERSION,
        "mode": str(identity.get("mode") or ""),
        "target_exists": target.exists(),
        "target_is_dir": target.is_dir(),
        "target_readable": target.exists() and os.access(target, os.R_OK),
        "target_writable": target.exists() and os.access(target, os.W_OK),
        "parent_exists": parent.is_dir(),
        "parent_writable": parent.is_dir() and os.access(parent, os.W_OK),
        "confined_to_projects_root": confined,
    }


def _project_preflight_context(
    session_id: str,
    body: ProjectPreflightRequest,
    request: Request,
) -> dict[str, Any]:
    proposal_context = _project_proposal_context(session_id, body, request)
    proposal = proposal_context["proposal"]
    if proposal["proposal_hash"] != body.proposal_hash:
        raise GuidedSetupConflict("guided_setup_project_proposal_stale")
    fixture_evidence = _resolve_project_fixture_evidence_refs(
        _db(request),
        session_id,
        body.fixture_evidence_refs,
    )
    path_observation = _observe_project_path(proposal_context["identity"])
    preflight = build_project_preflight(
        proposal_context["needs"],
        proposal,
        proposal_context["context"]["plan"],
        proposal_context["context"]["inventory"],
        path_observation,
        fixture_evidence=fixture_evidence,
    )
    execution_plan = build_project_preflight_execution_plan(
        proposal_context["needs"],
        proposal,
        preflight,
    )
    return {
        "proposal_context": proposal_context,
        "path_observation": path_observation,
        "preflight": preflight,
        "execution_plan": execution_plan,
    }


def _resolve_project_fixture_evidence_refs(
    db_path: Path,
    session_id: str,
    references: list[str],
) -> list[dict[str, Any]]:
    return resolve_project_fixture_evidence(
        db_path,
        session_id,
        references,
    )

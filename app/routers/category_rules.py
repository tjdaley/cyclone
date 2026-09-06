"""
app/routers/category_rules.py - Keyword rules that file transactions.

Thin, like every router here. The interesting decisions — never overwrite a
person, record which rule fired, try the longer pattern first — all live in
``category_rule_service``.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from db.models.financial import TransactionCategoryRule
from db.repositories.financial import (
    TransactionCategoryRepository,
    TransactionCategoryRuleRepository,
)
from db.repositories.matter import MatterRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.category_rule import (
    CategoryRuleResponse,
    CategoryRuleWriteRequest,
    RuleRunResult,
)
from services.category_rule_service import category_rule_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["category-rules"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]


def _response(record: Any, in_use: int = 0) -> CategoryRuleResponse:
    return CategoryRuleResponse(
        **record.model_dump(),
        is_firm_wide=record.matter_id is None,
        transactions_filed=in_use,
    )


@router.get("/category-rules", response_model=list[CategoryRuleResponse])
def list_rules(
    matter_id: Optional[int] = Query(
        default=None,
        description="Omit for the firm-wide rules; give a matter for that case's own",
    ),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[CategoryRuleResponse]:
    """
    One layer of rules, for editing.

    Deliberately one layer rather than the merged set a match uses: an editor
    that blurred them would offer to change a firm-wide rule from inside a
    matter, and quietly re-file every other case.
    """
    repo = TransactionCategoryRuleRepository(manager)
    rules = repo.for_scope(matter_id)
    counts = {rule.id: repo.in_use(rule.id) for rule in rules}
    return [_response(rule, counts.get(rule.id, 0)) for rule in
            sorted(rules, key=lambda r: (r.priority, r.pattern.lower()))]


@router.post("/category-rules", response_model=CategoryRuleResponse, status_code=201)
def create_rule(
    body: CategoryRuleWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> CategoryRuleResponse:
    """Write a rule. With no matter_id it applies to every case."""
    if TransactionCategoryRepository(manager).select_one(
            condition={"id": body.category_id}) is None:
        raise HTTPException(status_code=422, detail="No such category")
    if body.matter_id is not None and MatterRepository(manager).select_one(
            condition={"id": body.matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    try:
        created = TransactionCategoryRuleRepository(manager).insert(
            TransactionCategoryRule(**body.model_dump()).model_dump()
        )
    except KeyError as e:
        raise HTTPException(
            status_code=409,
            detail="A rule with that pattern already exists at this scope",
        ) from e
    LOGGER.info("category_rules: created rule=%s category=%s matter=%s",
                created.id, body.category_id, body.matter_id)
    return _response(created)


@router.patch("/category-rules/{rule_id}", response_model=CategoryRuleResponse)
def update_rule(
    rule_id: int,
    body: CategoryRuleWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> CategoryRuleResponse:
    """
    Change a rule.

    Editing does not re-file anything on its own. Re-running is a separate,
    deliberate act, because a rule change can move hundreds of lines and the
    person making it should choose when that happens.
    """
    repo = TransactionCategoryRuleRepository(manager)
    if repo.select_one(condition={"id": rule_id}) is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    updated = repo.update(rule_id, body.model_dump(exclude_none=True))
    return _response(updated, repo.in_use(rule_id))


@router.delete("/category-rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> None:
    """
    Remove a rule.

    The transactions it filed keep their category and keep pointing at the rule
    id. That is on purpose: the trail has to outlive the rule, and the moment
    somebody most wants it is right after deleting the rule that caused the
    problem they are investigating.
    """
    repo = TransactionCategoryRuleRepository(manager)
    if repo.select_one(condition={"id": rule_id}) is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    LOGGER.info("category_rules: deleting rule=%s which filed %d transaction(s)",
                rule_id, repo.in_use(rule_id))
    repo.delete(rule_id)


@router.post("/matters/{matter_id}/category-rules/run", response_model=RuleRunResult)
def run_rules(
    matter_id: int,
    request: Request,
    include_reviewed: bool = Query(
        default=False,
        description="Also re-file automatic assignments a person has already confirmed. "
                    "Off by default: confirming one is a decision",
    ),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> RuleRunResult:
    """
    Re-run the rules across a matter.

    This is what makes a growing keyword list worth maintaining: a rule written
    today reaches statements ingested last month. Lines a person filed are never
    touched.
    """
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    result = category_rule_service.apply_to_matter(
        manager, matter_id, include_reviewed=include_reviewed,
    )
    LOGGER.info("category_rules: matter=%s run by staff=%s -> %s",
                matter_id, staff.id if staff else None, result)
    return RuleRunResult(**result)

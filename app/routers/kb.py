"""
app/routers/kb.py - Admin-only CRUD for the CRM agent's knowledge base.

The agent reads these rows at compose time. Edits take effect immediately
on the next agent invocation — no restart, no cache to bust.
"""
from fastapi import APIRouter, Depends, HTTPException

from db.repositories.kb_article import KbArticleRepository
from dependencies import get_db_manager, require_role
from schemas.common import DeletedResponse
from schemas.kb_article import (
    KbArticleCreateRequest, KbArticleResponse, KbArticleUpdateRequest,
)
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/kb-articles", tags=["kb"])


@router.get("", response_model=list[KbArticleResponse])
def list_articles(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[KbArticleResponse]:
    """List every article (active + inactive) ordered for the editor."""
    records = KbArticleRepository(manager).list_all()
    return [KbArticleResponse(**r.model_dump()) for r in records]


@router.post("", response_model=KbArticleResponse, status_code=201)
def create_article(
    body: KbArticleCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> KbArticleResponse:
    """Create a new KB article."""
    repo = KbArticleRepository(manager)
    record = repo.insert(body.model_dump())
    LOGGER.info("kb.create: id=%s topic=%s", record.id, record.topic)
    return KbArticleResponse(**record.model_dump())


@router.patch("/{article_id}", response_model=KbArticleResponse)
def update_article(
    article_id: int,
    body: KbArticleUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> KbArticleResponse:
    """Partially update a KB article."""
    repo = KbArticleRepository(manager)
    if repo.select_one(condition={"id": article_id}) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    record = repo.update(article_id, updates)
    LOGGER.info("kb.update: id=%s", article_id)
    return KbArticleResponse(**record.model_dump())


@router.delete("/{article_id}", response_model=DeletedResponse)
def delete_article(
    article_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """Delete a KB article. Removed from the agent's prompt on next invocation."""
    repo = KbArticleRepository(manager)
    if repo.select_one(condition={"id": article_id}) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    LOGGER.info("kb.delete: id=%s", article_id)
    repo.delete(article_id)
    return DeletedResponse(id=article_id)

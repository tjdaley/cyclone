"""
app/util/schema_check.py - Startup check that the database matches the models.

A model field with no matching column is a live 500 waiting for the right
request: ``model_dump()`` includes a field even when it is None, and PostgREST
rejects the whole row for an unknown column (PGRST204). That is how
``clients.referred_to_staff_id`` sat broken until an intake commit hit it in
production — the failure surfaced as a stack trace during real work rather than
as a line in the deploy log.

The live schema comes from PostgREST's own OpenAPI document, which describes
every table including empty ones, so this needs no query access and no
per-table round trips. The table-to-model mapping comes from the repositories,
the only authoritative source for it — each one hands its table name and model
class to ``BaseRepository.__init__``.

Read-only, one HTTP call, and every failure is swallowed: a check that cannot
run must never stop the API from starting.
"""
import importlib
import inspect
import pathlib
from typing import Any, Optional

import httpx

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_REPO_DIR = pathlib.Path(__file__).resolve().parent.parent / "db" / "repositories"
_TIMEOUT_SECONDS = 10.0


def _live_columns() -> Optional[dict[str, set[str]]]:
    """
    Fetch every table's columns from PostgREST's OpenAPI document.

    :return: Table name -> column names, or None when the schema is unreadable.
    :rtype: Optional[dict[str, set[str]]]
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        LOGGER.warning("schema_check: skipped — Supabase URL or service role key is not configured")
        return None
    try:
        response = httpx.get(
            settings.supabase_url.rstrip("/") + "/rest/v1/",
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": "Bearer " + settings.supabase_service_role_key,
                "Accept": "application/openapi+json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        definitions = response.json().get("definitions") or {}
    except Exception as e:  # noqa: BLE001 — a diagnostic must not break startup
        LOGGER.warning("schema_check: could not read the live schema: %s", str(e))
        return None

    return {name: set(spec.get("properties", {})) for name, spec in definitions.items()}


def _table_models() -> dict[str, Any]:
    """
    Map table names to the model each repository reads them into.

    Repositories are instantiated with a ``None`` manager purely to read the
    table name and model class they register; ``BaseRepository.__init__`` only
    records them. Any repository that does more in its constructor is skipped
    rather than allowed to fail the check.

    :return: Table name -> Pydantic model class.
    :rtype: dict[str, Any]
    """
    from db_handler import BaseRepository  # noqa: PLC0415 — keeps import order simple

    mapping: dict[str, Any] = {}
    for path in sorted(_REPO_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        try:
            module = importlib.import_module("db.repositories.%s" % path.stem)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("schema_check: could not import %s: %s", path.stem, str(e))
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseRepository) or obj is BaseRepository:
                continue
            try:
                instance = obj(None)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — constructor needs more than a table name
                continue
            table = getattr(instance, "table_name", None)
            model = getattr(instance, "model_class", None)
            if table and model is not None:
                mapping[table] = model
    return mapping


def check_schema() -> list[str]:
    """
    Compare every mapped table against its model.

    Only reports fields the model declares that the table lacks — the direction
    that breaks inserts. Extra columns the model ignores are harmless (Pydantic
    drops them on read) and are logged at DEBUG instead.

    :return: One description per mismatch; empty when the schema is sound.
    :rtype: list[str]
    """
    live = _live_columns()
    if live is None:
        return []  # Already logged why; a check that cannot run is not a failure

    problems: list[str] = []
    checked = 0
    for table, model in sorted(_table_models().items()):
        columns = live.get(table)
        if columns is None:
            continue  # A table the database does not have — a pending migration
        checked += 1

        fields = set(model.model_fields)
        missing = sorted(fields - columns)
        if missing:
            problems.append(
                "table '%s' is missing %s — every insert through %s will fail (PGRST204)"
                % (table, ", ".join(missing), model.__name__)
            )
        unused = sorted(columns - fields)
        if unused:
            LOGGER.debug("schema_check: %s has columns the model ignores: %s", table, unused)

    LOGGER.info("schema_check: %d tables verified, %d mismatch(es)", checked, len(problems))
    return problems

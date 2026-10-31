"""
app/services/guest_access_service.py - A financial professional's access to a matter (052).

Design and decisions: docs/specs/financial-guest-access.md. What must not be
relearned:

STAFF ROUTES ARE NOT MATTER-SCOPED, SO NONE OF THEM IS REUSED
────────────────────────────────────────────────────────────────────────────
Any staff role can open any matter. A guest is retained on ONE case (or a few),
so every guest endpoint resolves its scope here — ``require_grant`` and the
``require_*`` helpers built on it — and nowhere else. ``SupabaseManager``
bypasses RLS; the database will not catch a mistake. ``GuestDenied`` answers
404, never 403, so a guest cannot learn which matter ids exist.

A GRANT IS RE-READ ON EVERY REQUEST
────────────────────────────────────────────────────────────────────────────
Nothing about access is cached in the JWT or the browser. Revocation takes
effect on the next request, and ``expired`` is derived from ``expires_at`` at
that moment rather than written by a job — a job that did not run would leave
access open.

THE DOCUMENT LEDGER IS SYNCHRONISED, NOT HOOKED
────────────────────────────────────────────────────────────────────────────
Disclosure is at the document level (Tom): what statements the guest was
provided. A rejected statement is hard-deleted, so the record must outlive it.
Rather than hook every ingest and delete path, each guest request that touches
a matter brings the ledger up to date: new statements are added, vanished ones
stamped ``removed_at``. That records exactly what was available while the guest
was using the system — which is what "provided" means.

WHAT A GUEST SEES IS SHAPED HERE AND IN schemas/guest.py
────────────────────────────────────────────────────────────────────────────
Their own tags only — the search decorates each line with every tag, so the
list is filtered before it leaves. No account notes or purpose, no statement
flags or provenance, no removed lines, no "untagged" filter (it would reveal
that staff tagged a line), no inclusion of deleted rows.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from db.models.financial import TagSource, TransactionTag, TransactionTagLink
from db.models.guest import (
    ConsentMethod,
    GrantStatus,
    GuestConsent,
    GuestDesignation,
    GuestDocumentLedgerEntry,
    GuestProfessional,
    GuestSignIn,
    MatterGuestGrant,
)
from db.models.user_role import UserRole, UserRoleType
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
    TransactionTagLinkRepository,
    TransactionTagRepository,
)
from db.repositories.guest import (
    GuestConsentRepository,
    GuestDocumentLedgerRepository,
    GuestProfessionalRepository,
    GuestSignInRepository,
    MatterGuestGrantRepository,
)
from db.repositories.matter import MatterRepository
from db.repositories.user_role import UserRoleRepository
from db_handler import DatabaseManager
from services.exhibit_service import Exhibit
from services.inventory_service import inventory_service
from services.transaction_search_service import transaction_search_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

#: How long a grant lasts unless somebody says otherwise. Renewable.
DEFAULT_GRANT_DAYS = 180
_MAX_GRANT_DAYS = 730

#: Said to the guest on activation and on their first screen.
PURPOSE_REMINDER = (
    "Your access exists so that you can use the Cyclone transaction database on behalf "
    "of, and solely for the benefit of, the client. Please do not use it, or anything "
    "you export from it, for any other purpose."
)


class GuestDenied(Exception):
    """Outside the guest's grants. The route answers 404 and never says why."""


class GuestConflict(Exception):
    """A staff action that cannot be taken in the grant's current state."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value(x: Any) -> Any:
    return getattr(x, "value", x)


def effective_status(grant: Any, guest: Optional[Any] = None, now: Optional[datetime] = None) -> str:
    """
    What a grant actually allows right now: pending_consent, active, expired or revoked.

    The stored status never says ``expired``; that is this function's call, made
    at the moment of asking, so access can never outlive its date because a job
    did not run. A deactivated guest makes every grant read as revoked.
    """
    status = _value(grant.status)
    if status == GrantStatus.revoked.value:
        return "revoked"
    if guest is not None and not getattr(guest, "is_active", True):
        return "revoked"
    expires = grant.expires_at
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if status == GrantStatus.active.value:
        return "active" if expires > (now or _now()) else "expired"
    return "pending_consent"


def _account_label(account: Any) -> str:
    last4 = getattr(account, "account_number_last4", None)
    return "%s%s" % (account.institution or "Unknown institution",
                     " ····%s" % last4 if last4 else "")


class GuestAccessService:
    """Staff management of guests, and everything a guest may see or do."""

    # ══ Staff side ══════════════════════════════════════════════════════════

    def grants_for_matter(self, manager: DatabaseManager, matter_id: int) -> list[dict[str, Any]]:
        """Every grant on a matter, newest first, with its guest, consent and ledger size."""
        grants = MatterGuestGrantRepository(manager).for_matter(matter_id)
        if not grants:
            return []
        guests = {g.id: g for g in GuestProfessionalRepository(manager).select_many(
            condition={"id": sorted({g.guest_id for g in grants})})[0]}
        consents: dict[int, list[Any]] = {}
        for c in GuestConsentRepository(manager).for_grants([g.id for g in grants]):
            consents.setdefault(c.grant_id, []).append(c)
        sign_ins = GuestSignInRepository(manager)
        ledger = GuestDocumentLedgerRepository(manager)
        out = []
        for grant in sorted(grants, key=lambda g: g.id, reverse=True):
            guest = guests.get(grant.guest_id)
            latest = sign_ins.latest(grant.guest_id)
            out.append({
                "grant": grant,
                "guest": guest,
                "effective_status": effective_status(grant, guest),
                "consents": consents.get(grant.id, []),
                "documents_provided": len(ledger.for_grant(grant.id)),
                "last_signed_in_at": latest.signed_in_at if latest else None,
            })
        return out

    def invite(
        self,
        manager: DatabaseManager,
        matter_id: int,
        full_name: str,
        email: str,
        designation: GuestDesignation,
        staff_id: int,
        staff_name: str,
        organization: Optional[str] = None,
        profession: Optional[str] = None,
        purpose: Optional[str] = None,
        expires_in_days: int = DEFAULT_GRANT_DAYS,
    ) -> Any:
        """
        Give a financial professional a grant on this matter, awaiting consent.

        Finds the guest by email or creates them, makes sure they have a role row
        to sign in with, and opens a ``pending_consent`` grant. Nothing is
        visible to them until consent is attested.

        :raises ValueError: Bad input, or an address that already signs in as
            staff or a client — one login holding both a staff role and a guest
            role would land in the staff app with every matter open.
        :raises GuestConflict: A live grant for this guest on this matter exists.
        """
        email = (email or "").strip()
        full_name = (full_name or "").strip()
        if not full_name:
            raise ValueError("Give the professional's name")
        if "@" not in email or " " in email:
            raise ValueError("Give the email address they will sign in with")
        if not 1 <= int(expires_in_days) <= _MAX_GRANT_DAYS:
            raise ValueError("Access lasts between 1 and %d days" % _MAX_GRANT_DAYS)
        if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
            raise LookupError("Matter not found")

        role_repo = UserRoleRepository(manager)
        rows = [r for r in role_repo.select_many(condition={})[0]
                if (r.auth_email or "").strip().lower() == email.lower()]
        if any(_value(r.role) != UserRoleType.financial_professional.value for r in rows):
            raise ValueError("That address already signs in to Cyclone as staff or a client. "
                             "Use a different address for guest access.")

        guest_repo = GuestProfessionalRepository(manager)
        guest = guest_repo.by_email(email)
        if guest is None:
            guest = guest_repo.insert(GuestProfessional(
                full_name=full_name, email=email,
                organization=(organization or "").strip() or None,
                profession=(profession or "").strip() or None,
                created_by_staff_id=staff_id,
            ).model_dump())
        elif not guest.is_active:
            guest = guest_repo.update(guest.id, {"is_active": True})

        if not any(getattr(r, "guest_id", None) == guest.id for r in rows):
            role_repo.insert(UserRole(
                auth_email=email, role=UserRoleType.financial_professional, guest_id=guest.id,
            ).model_dump())

        grant_repo = MatterGuestGrantRepository(manager)
        if any(_value(g.status) != GrantStatus.revoked.value
               for g in grant_repo.for_guest(guest.id) if g.matter_id == matter_id):
            raise GuestConflict("%s already has access to this matter, or is awaiting consent"
                                % guest.full_name)

        grant = grant_repo.insert(MatterGuestGrant(
            matter_id=matter_id, guest_id=guest.id, designation=designation,
            purpose=(purpose or "").strip() or None,
            expires_at=_now() + timedelta(days=int(expires_in_days)),
            requested_by_staff_id=staff_id, requested_by=staff_name,
        ).model_dump())
        LOGGER.info("guest_access.invite: matter=%s guest=%s grant=%s staff=%s",
                    matter_id, guest.id, grant.id, staff_id)
        return grant

    def _grant_on_matter(self, manager: DatabaseManager, matter_id: int, grant_id: int) -> Any:
        grant = MatterGuestGrantRepository(manager).select_one(condition={"id": grant_id})
        if grant is None or grant.matter_id != matter_id:
            raise LookupError("No such access on this matter")
        return grant

    def attest_consent(
        self,
        manager: DatabaseManager,
        matter_id: int,
        grant_id: int,
        method: ConsentMethod,
        consent_date: date,
        staff_id: int,
        staff_name: str,
        note: Optional[str] = None,
    ) -> tuple[Any, Optional[dict[str, str]]]:
        """
        Record that the client consented, and open the grant.

        :return: ``(grant, email)`` — the email to send the guest, as
            ``{"to", "subject", "body"}``, or None if they have no address. The
            route sends it after responding: mail must not hold the request.
        :raises GuestConflict: The grant is not awaiting consent.
        """
        grant = self._grant_on_matter(manager, matter_id, grant_id)
        if _value(grant.status) != GrantStatus.pending_consent.value:
            raise GuestConflict("This access is not awaiting consent")
        if consent_date > date.today():
            raise ValueError("Consent cannot be dated in the future")

        GuestConsentRepository(manager).insert(GuestConsent(
            grant_id=grant_id, method=method, consent_date=consent_date,
            note=(note or "").strip() or None,
            attested_by_staff_id=staff_id, attested_by=staff_name,
        ).model_dump())
        grant = MatterGuestGrantRepository(manager).update(grant_id, {
            "status": GrantStatus.active.value, "activated_at": _now(),
        })
        # Seed the ledger now: the documents are available from this moment,
        # whether or not the guest signs in today.
        self.sync_ledger(manager, grant)
        LOGGER.info("guest_access.attest_consent: matter=%s grant=%s staff=%s",
                    matter_id, grant_id, staff_id)

        guest = GuestProfessionalRepository(manager).select_one(condition={"id": grant.guest_id})
        matter = MatterRepository(manager).select_one(condition={"id": matter_id})
        if guest is None or not guest.email:
            return grant, None
        return grant, self._activation_email(guest, matter, grant)

    @staticmethod
    def _activation_email(guest: Any, matter: Any, grant: Any) -> dict[str, str]:
        from util.settings import settings

        matter_name = getattr(matter, "short_name", None) or getattr(matter, "matter_name", "the matter")
        expires = grant.expires_at
        expires_text = expires.strftime("%B %d, %Y") if hasattr(expires, "strftime") else str(expires)
        body = (
            "Dear %s,\n\n"
            "The client has consented to your access to the financial records in %s, "
            "through Cyclone, the case-management system used by %s.\n\n"
            "%s\n\n"
            "You can see the accounts, statements, transactions and account inventory for the "
            "matter, export them, and tag transactions with tags of your own. Your access runs "
            "until %s unless it is renewed or withdrawn earlier.\n\n"
            "Sign in at %s/login with this email address.\n\n"
            "%s\n"
        ) % (guest.full_name, matter_name, settings.firm_name or "the firm",
             PURPOSE_REMINDER, expires_text, (settings.host_url or "").rstrip("/"),
             settings.firm_name or "")
        return {"to": guest.email, "subject": "Access to %s financial records" % matter_name,
                "body": body}

    def revoke(
        self, manager: DatabaseManager, matter_id: int, grant_id: int,
        staff_id: int, staff_name: str, reason: Optional[str] = None,
    ) -> Any:
        """Withdraw one grant. Effective on the guest's next request."""
        grant = self._grant_on_matter(manager, matter_id, grant_id)
        if _value(grant.status) == GrantStatus.revoked.value:
            return grant
        updated = MatterGuestGrantRepository(manager).update(grant_id, {
            "status": GrantStatus.revoked.value, "revoked_at": _now(),
            "revoked_by_staff_id": staff_id, "revoked_by": staff_name,
            "revoke_reason": (reason or "").strip() or None,
        })
        LOGGER.info("guest_access.revoke: matter=%s grant=%s staff=%s", matter_id, grant_id, staff_id)
        return updated

    def revoke_everywhere(
        self, manager: DatabaseManager, guest_id: int, staff_id: int, staff_name: str,
        reason: Optional[str] = None,
    ) -> int:
        """Withdraw every live grant a guest holds, on every matter. Returns how many."""
        count = 0
        for grant in MatterGuestGrantRepository(manager).for_guest(guest_id):
            if _value(grant.status) != GrantStatus.revoked.value:
                self.revoke(manager, grant.matter_id, grant.id, staff_id, staff_name, reason)
                count += 1
        return count

    def renew(self, manager: DatabaseManager, matter_id: int, grant_id: int, days: int) -> Any:
        """Set a grant to expire ``days`` from now. A revoked grant cannot be renewed."""
        if not 1 <= int(days) <= _MAX_GRANT_DAYS:
            raise ValueError("Access lasts between 1 and %d days" % _MAX_GRANT_DAYS)
        grant = self._grant_on_matter(manager, matter_id, grant_id)
        if _value(grant.status) == GrantStatus.revoked.value:
            raise GuestConflict("Revoked access cannot be renewed; grant it again")
        return MatterGuestGrantRepository(manager).update(grant_id, {
            "expires_at": _now() + timedelta(days=int(days))})

    def set_designation(
        self, manager: DatabaseManager, matter_id: int, grant_id: int,
        designation: GuestDesignation,
    ) -> Any:
        """Change testifying / consulting / non-expert. Audited by the route."""
        self._grant_on_matter(manager, matter_id, grant_id)
        return MatterGuestGrantRepository(manager).update(grant_id, {"designation": designation})

    def ledger(self, manager: DatabaseManager, matter_id: int, grant_id: int) -> list[Any]:
        """
        What the guest was provided under one grant. Read only — a staff member
        looking at it does not make anything available to the guest.
        """
        self._grant_on_matter(manager, matter_id, grant_id)
        return GuestDocumentLedgerRepository(manager).for_grant(grant_id)

    def ledger_exhibit(
        self, manager: DatabaseManager, matter: Any, grant_id: int, name: Optional[str] = None,
    ) -> Exhibit:
        """The ledger as a document, for a disclosure."""
        from services.exhibit_service import Column, caption_lines

        grant = self._grant_on_matter(manager, matter.id, grant_id)
        guest = GuestProfessionalRepository(manager).select_one(condition={"id": grant.guest_id})
        name = (name or "").strip() or "Documents Provided to %s" % (
            guest.full_name if guest else "Financial Professional")
        caption, warnings = caption_lines(matter, name)
        rows = []
        for entry in GuestDocumentLedgerRepository(manager).for_grant(grant_id):
            rows.append((
                entry.account_label or "",
                "%s – %s" % (entry.period_start or "", entry.period_end or ""),
                entry.source_filename or "",
                "%s – %s" % (entry.bates_first, entry.bates_last)
                if entry.bates_first and entry.bates_last else (entry.bates_first or ""),
                entry.first_available_at.strftime("%m/%d/%Y") if entry.first_available_at else "",
                entry.removed_at.strftime("%m/%d/%Y") if entry.removed_at else "",
            ))
        return Exhibit(
            name=name,
            caption=caption,
            columns=(Column("Account"), Column("Period"), Column("Document"), Column("Bates"),
                     Column("Available from"), Column("Removed")),
            rows=tuple(rows),
            selection=(
                ("Provided to", guest.full_name if guest else "—"),
                ("Designation", _value(grant.designation).replace("_", " ")),
                ("What this lists", "Every statement made available to this person through "
                                    "Cyclone under this grant, with the date it first was and, "
                                    "where it no longer exists, the date it was removed"),
            ),
            warnings=warnings,
        )

    # ══ Guest side: scope ═══════════════════════════════════════════════════

    def _guest(self, manager: DatabaseManager, guest_id: int) -> Any:
        guest = GuestProfessionalRepository(manager).select_one(condition={"id": guest_id})
        if guest is None or not guest.is_active:
            raise GuestDenied("No access")
        return guest

    def active_grants(self, manager: DatabaseManager, guest_id: int) -> list[Any]:
        """The grants that open something right now."""
        guest = self._guest(manager, guest_id)
        now = _now()
        return [g for g in MatterGuestGrantRepository(manager).for_guest(guest_id)
                if effective_status(g, guest, now) == "active"]

    def require_grant(self, manager: DatabaseManager, guest_id: int, matter_id: int) -> Any:
        """
        The guest's live grant on this matter, or ``GuestDenied``.

        Every guest read and write goes through here, and here is where the
        document ledger is brought up to date.
        """
        grant = next((g for g in self.active_grants(manager, guest_id)
                      if g.matter_id == matter_id), None)
        if grant is None:
            raise GuestDenied("No access")
        self.sync_ledger(manager, grant)
        return grant

    def require_account(self, manager: DatabaseManager, guest_id: int, account_id: int) -> Any:
        account = FinancialAccountRepository(manager).select_one(condition={"id": account_id})
        if account is None:
            raise GuestDenied("No access")
        self.require_grant(manager, guest_id, account.matter_id)
        return account

    def require_statement(self, manager: DatabaseManager, guest_id: int, statement_id: int) -> Any:
        statement = FinancialAccountStatementRepository(manager).select_one(
            condition={"id": statement_id})
        if statement is None or _value(statement.review_status) == "rejected":
            raise GuestDenied("No access")
        self.require_account(manager, guest_id, statement.financial_account_id)
        return statement

    # ══ Guest side: the ledger ══════════════════════════════════════════════

    def sync_ledger(self, manager: DatabaseManager, grant: Any) -> None:
        """
        Bring the grant's document ledger up to date with the matter's statements.

        Never fails the request: the ledger is a record of what was available,
        and a write that did not happen is logged at ERROR rather than turned
        into a guest who cannot read their matter.
        """
        try:
            accounts = {a.id: a for a in FinancialAccountRepository(manager).get_by_matter(
                grant.matter_id)}
            statements = [s for s in FinancialAccountStatementRepository(manager).get_by_matter(
                grant.matter_id) if _value(s.review_status) != "rejected"]
            repo = GuestDocumentLedgerRepository(manager)
            held = {e.statement_id: e for e in repo.for_grant(grant.id)}
            now = _now()
            present = set()
            for statement in statements:
                present.add(statement.id)
                if statement.id in held:
                    continue
                extraction = statement.extraction or {}
                account = accounts.get(statement.financial_account_id)
                repo.insert(GuestDocumentLedgerEntry(
                    grant_id=grant.id, statement_id=statement.id,
                    financial_account_id=statement.financial_account_id,
                    account_label=_account_label(account) if account else None,
                    period_start=statement.period_start, period_end=statement.period_end,
                    source_filename=extraction.get("source_filename"),
                    bates_first=extraction.get("bates_first"),
                    bates_last=extraction.get("bates_last"),
                    first_available_at=now,
                ).model_dump())
            for statement_id, entry in held.items():
                if statement_id not in present and entry.removed_at is None:
                    repo.update(entry.id, {"removed_at": now})
        except Exception as e:  # noqa: BLE001 - see docstring
            LOGGER.error("guest_access.sync_ledger: grant=%s failed: %s",
                         getattr(grant, "id", None), type(e).__name__)

    def record_sign_in(self, manager: DatabaseManager, guest_id: int) -> None:
        """A guest opened the app. For revocation audits; never fails the request."""
        try:
            GuestSignInRepository(manager).insert(
                GuestSignIn(guest_id=guest_id, signed_in_at=_now()).model_dump())
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("guest_access.record_sign_in: guest=%s failed: %s",
                           guest_id, type(e).__name__)

    # ══ Guest side: reading ═════════════════════════════════════════════════

    def matters(self, manager: DatabaseManager, guest_id: int) -> list[dict[str, Any]]:
        """The matters the guest may open, with what their access is."""
        out = []
        repo = MatterRepository(manager)
        for grant in self.active_grants(manager, guest_id):
            matter = repo.select_one(condition={"id": grant.matter_id})
            if matter is None:
                continue
            out.append({"matter": matter, "grant": grant})
        return out

    def accounts(self, manager: DatabaseManager, guest_id: int, matter_id: int) -> list[Any]:
        self.require_grant(manager, guest_id, matter_id)
        return FinancialAccountRepository(manager).get_by_matter(matter_id)

    def statements(self, manager: DatabaseManager, guest_id: int, account_id: int) -> list[Any]:
        self.require_account(manager, guest_id, account_id)
        return [s for s in FinancialAccountStatementRepository(manager).get_by_account(account_id)
                if _value(s.review_status) != "rejected"]

    def statement_transactions(
        self, manager: DatabaseManager, guest_id: int, statement_id: int,
    ) -> list[Any]:
        """A statement's lines. Removed lines are not shown — include_deleted stays off."""
        self.require_statement(manager, guest_id, statement_id)
        return FinancialAccountTransactionRepository(manager).get_by_statement(statement_id)

    def own_tags(
        self, manager: DatabaseManager, guest_id: int, matter_id: int,
        include_inactive: bool = True,
    ) -> list[Any]:
        """This guest's tags on this matter, and nobody else's."""
        rows = TransactionTagRepository(manager).select_many(
            condition={"matter_id": matter_id, "guest_id": guest_id},
            sort_by="display_order")[0]
        return rows if include_inactive else [t for t in rows if t.is_active]

    def _sanitize_filters(
        self, manager: DatabaseManager, guest_id: int, matter_id: int, filters: dict[str, Any],
    ) -> dict[str, Any]:
        """
        A staff search request, made safe for a guest.

        Tag filters are narrowed to the guest's own tags — anything else is a
        probe for which lines staff tagged. "Untagged" is refused for the same
        reason, and removed lines are never included.
        """
        clean = dict(filters)
        own = {t.id for t in self.own_tags(manager, guest_id, matter_id)}
        if clean.get("tag_ids"):
            clean["tag_ids"] = [i for i in clean["tag_ids"] if i in own]
            if not clean["tag_ids"]:
                # Asked only for tags that are not theirs: an empty answer, not
                # an unfiltered one.
                clean["tag_ids"] = [-1]
        clean["untagged"] = False
        clean["include_deleted"] = False
        return clean

    def search(
        self, manager: DatabaseManager, guest_id: int, matter_id: int, filters: dict[str, Any],
    ) -> dict[str, Any]:
        """The staff search, scoped and filtered, with only the guest's own tag ids."""
        self.require_grant(manager, guest_id, matter_id)
        clean = self._sanitize_filters(manager, guest_id, matter_id, filters)
        result = transaction_search_service.search(manager, matter_id, **clean)
        own = {t.id for t in self.own_tags(manager, guest_id, matter_id)}
        for row in result["items"]:
            row["tag_ids"] = [i for i in row.get("tag_ids", []) if i in own]
            row.pop("client_tag_ids", None)
        return result

    def inventory(self, manager: DatabaseManager, guest_id: int, matter_id: int) -> dict[str, Any]:
        """The inventory as staff see it, without who excluded what."""
        self.require_grant(manager, guest_id, matter_id)
        matter = MatterRepository(manager).select_one(condition={"id": matter_id})
        report = inventory_service.inventory(manager, matter_id, matter=matter)
        for row in report["assets"] + report["liabilities"]:
            row["excluded_by"] = None
            row["excluded_at"] = None
        for rate in report.get("rates", []):
            rate["entered_by"] = None
        return report

    # ══ Guest side: exports ═════════════════════════════════════════════════

    def _footer(self, manager: DatabaseManager, guest_id: int) -> str:
        guest = self._guest(manager, guest_id)
        return "Prepared for %s" % guest.full_name

    def export_inventory(
        self, manager: DatabaseManager, guest_id: int, matter_id: int, name: str,
    ) -> Exhibit:
        self.require_grant(manager, guest_id, matter_id)
        matter = MatterRepository(manager).select_one(condition={"id": matter_id})
        exhibit = inventory_service.build_exhibit(manager, matter, name)
        exhibit.footer = self._footer(manager, guest_id)
        return exhibit

    def export_transactions(
        self, manager: DatabaseManager, guest_id: int, matter_id: int, name: str,
        criteria: dict[str, Any],
    ) -> Exhibit:
        self.require_grant(manager, guest_id, matter_id)
        matter = MatterRepository(manager).select_one(condition={"id": matter_id})
        clean = self._sanitize_filters(manager, guest_id, matter_id, criteria)
        exhibit = transaction_search_service.build_exhibit(manager, matter, name, clean)
        exhibit.footer = self._footer(manager, guest_id)
        return exhibit

    # ══ Guest side: the one write ═══════════════════════════════════════════

    def create_tag(
        self, manager: DatabaseManager, guest_id: int, matter_id: int,
        label: str, description: Optional[str] = None, color: Optional[str] = None,
    ) -> Any:
        self.require_grant(manager, guest_id, matter_id)
        label = (label or "").strip()
        if not label:
            raise ValueError("A tag needs a label")
        if any(t.label.strip().lower() == label.lower()
               for t in self.own_tags(manager, guest_id, matter_id)):
            raise ValueError("You already have a tag called that")
        return TransactionTagRepository(manager).insert(TransactionTag(
            matter_id=matter_id, guest_id=guest_id, label=label,
            description=(description or "").strip() or None, color=color,
        ).model_dump())

    def _own_tag(self, manager: DatabaseManager, guest_id: int, tag_id: int) -> Any:
        tag = TransactionTagRepository(manager).select_one(condition={"id": tag_id})
        if tag is None or tag.guest_id != guest_id or tag.matter_id is None:
            raise GuestDenied("No access")
        self.require_grant(manager, guest_id, tag.matter_id)
        return tag

    def update_tag(self, manager: DatabaseManager, guest_id: int, tag_id: int,
                   updates: dict[str, Any]) -> Any:
        self._own_tag(manager, guest_id, tag_id)
        allowed = {k: v for k, v in updates.items()
                   if k in ("label", "description", "color", "is_active", "display_order")}
        if "label" in allowed and not (allowed["label"] or "").strip():
            raise ValueError("A tag needs a label")
        if not allowed:
            raise ValueError("Nothing to change")
        return TransactionTagRepository(manager).update(tag_id, allowed)

    def delete_tag(self, manager: DatabaseManager, guest_id: int, tag_id: int) -> None:
        """Their own tag, and every place they applied it."""
        self._own_tag(manager, guest_id, tag_id)
        link_repo = TransactionTagLinkRepository(manager)
        for link in link_repo.select_many(condition={"tag_id": tag_id})[0]:
            link_repo.delete(link.id)
        TransactionTagRepository(manager).delete(tag_id)

    def apply_tag(
        self, manager: DatabaseManager, guest_id: int, matter_id: int,
        transaction_ids: list[int], tag_id: int, remove: bool = False,
    ) -> int:
        """
        Put their own tag on lines of their matter, or take it off.

        :return: How many links changed.
        :raises GuestDenied: Not their tag, not their matter, or a line elsewhere.
        """
        tag = self._own_tag(manager, guest_id, tag_id)
        if tag.matter_id != matter_id:
            raise GuestDenied("No access")
        try:
            verified = transaction_search_service._verify_on_matter(
                manager, matter_id, transaction_ids)
        except ValueError as e:
            raise GuestDenied("No access") from e

        link_repo = TransactionTagLinkRepository(manager)
        changed = 0
        for transaction_id in verified:
            existing = link_repo.find_link(transaction_id, tag_id)
            if remove:
                if existing is not None:
                    link_repo.delete(existing.id)
                    changed += 1
            elif existing is None:
                link_repo.insert(TransactionTagLink(
                    transaction_id=transaction_id, tag_id=tag_id,
                    source=TagSource.guest, tagged_by_guest_id=guest_id,
                ).model_dump())
                changed += 1
        LOGGER.info("guest_access.apply_tag: matter=%s tag=%s guest=%s %s=%d",
                    matter_id, tag_id, guest_id, "removed" if remove else "applied", changed)
        return changed

    def statement_pdf_path(self, manager: DatabaseManager, guest_id: int, statement_id: int) -> Any:
        """The statement, once scope is checked; the route signs its storage path."""
        return self.require_statement(manager, guest_id, statement_id)


guest_access_service = GuestAccessService()

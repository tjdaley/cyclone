# 6. Merging accounts

[← When an import has a problem](05-import-problems.md) · [Index](README.md) · Next: [Viewing statements →](07-viewing-statements.md)

---

## Why this happens

Cyclone matches a statement to an existing account on **bank name + last four
digits**. When either half is misread, the statement does not match, and a new
account is created.

The usual cause is that **many banks print their name only inside the letterhead
graphic** — a picture, not text. The first upload files the account under
"Unknown institution". You correct the name afterwards, which is the right
thing to do, but it does not go back and change what the *next* upload matches
against. So the second statement opens a second row for the same real account.

The result is one real account showing up as two, each holding half the
history — and once statements pile up, that is genuinely hard to see. It is
worth a deliberate look at the **Accounts** tab after every import.

## How to spot it

On the **Accounts** tab, look for:

- Two rows at the same bank with the same last four.
- Two rows with the same last four under different bank names.
- An account named **Unknown institution** or **Unidentified institution**.
- Two accounts whose statement periods interleave rather than overlap — one has
  January, March, May; the other February, April, June.
- Any account whose import raised **NO_ACCOUNT_MATCH** or
  **SAME_LAST4_DIFFERENT_INSTITUTION**.

---

## Doing the merge

Decide first **which account you are keeping**. Everything moves onto the
account you keep, and the other one is deleted.

1. **Accounts** tab → find the account you want to get rid of → **Edit**.
2. At the bottom of the editor, expand **Merge into another account**.
3. In the dropdown — *"keep which account?"* — choose the account you are
   **keeping**.
4. A preview appears immediately. Read it.
5. Click **Merge** (or **Merge anyway**, if there are non-blocking conflicts).

### The preview

> *Moves 7 statements and 412 transactions onto Wells Fargo ····4448, then
> deletes Unknown institution ····4448.*

Any problems are listed below it, red for blocking and amber for the rest.

Merging moves evidence and deletes a row, so it **always** previews first.
Read the preview every time.

---

## Merge conflicts

### Blocking — these stop the merge

| Conflict | What it means | What to do |
| -------- | ------------- | ---------- |
| **PERIOD_OVERLAP** | Both accounts hold a statement covering the same period. The same period cannot sit twice on one account. | Work out which of the two duplicate statements is the better read, and **reject the other one** ([Chapter 5](05-import-problems.md)). Then merge. |
| **SAME_ACCOUNT** | You picked the same account as source and destination. | Pick a different destination. |
| **DIFFERENT_MATTER** | The two accounts belong to different matters. | Moving records between matters is never a merge. If a statement was imported onto the wrong matter, reject it there and re-import it on the right one. |

### Non-blocking — you can proceed, but read them

The button changes to **Merge anyway** when one of these is present.

| Conflict | What it means | What to do |
| -------- | ------------- | ---------- |
| **BATES_OVERLAP** | Named pages are already on the destination account. The same pages appear to have been ingested twice. | Check whether one of the statements is a duplicate. If so, reject it first. Merging over this leaves duplicated pages on one account. |
| **LAST4_MISMATCH** | The account numbers end differently. **These may be two real accounts.** | Stop and check the source statements. Do not merge two different accounts to tidy up a list. |
| **TYPE_MISMATCH** | One is recorded as (say) checking and the other as savings. | Usually one was typed wrong; fix the type first, or merge and then correct it. But a checking and a savings account at the same bank genuinely can share a last four in some banks' numbering. |

---

## Related repairs on the same screen

### Succeeds account

Not every pair of accounts should be merged. When one account genuinely
*replaced* another — a reissued credit card, a bank migration, a rollover —
they are two accounts with a real relationship, not one account filed twice.

In the account editor, set **Succeeds account** to the earlier one. Three
accounts where each succeeds the next look like three half-produced accounts;
linked, they read as one history and the apparent gaps close.

### Delete this account

Also at the bottom of the account editor, in red. For an account that exists
only because an extraction misread the institution, or a statement imported
onto the wrong matter. It previews first, showing exactly how many statements
and transactions go with it.

**Prefer merging.** Deleting throws the statements away (they can be
re-uploaded, but that is work); merging keeps them.

The preview warns you when the account carries attorney work — recorded
ownership, characterization, purpose, or notes. That work was not produced by
the import and should not be thrown away by one.

---

## While you are on the Accounts tab

Every account also needs your judgment recorded. These are never extracted.

| Field | Why it matters |
| ----- | -------------- |
| **Held by** | `Our client, solely` / `Other party, solely` / `Jointly held` / `A third party` / `Not yet determined`. **Joint decides whether an asset divides**, so it is never inferred. The Accounts tab count in amber is the number still set to "Not yet determined". |
| **Who produces** | Which compliance report this account appears on — see [Chapter 11](11-discovery-tracking.md). Usually follows "Held by", and sometimes does not. |
| **Characterization** | Community, separate (Petitioner or Respondent), mixed, or disputed. An uncharacterized account shows an amber chip. |
| **Other party / Joint with** | Name the other party for a joint or opposing account. Division turns on who holds it. |
| **Purpose** | *Household operating account, business payroll, …* |
| **Notes** | |
| **Account is closed** | |

---

[← When an import has a problem](05-import-problems.md) · [Index](README.md) · Next: [Viewing statements →](07-viewing-statements.md)

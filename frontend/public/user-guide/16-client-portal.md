# 16. Letting a client in

[← AI costs and prices](15-costs.md) · [Index](README.md)

---

A client can be given a read-only view of their own matter: the accounts, the
statements the office holds, every transaction, and which months are still
missing. They can also be allowed to **tag** transactions — to mark what they
think somebody should look at.

**Everything a client marks is a note, not a finding.** They change nothing in
the record. This chapter is the three jobs on the staff side: switching it on,
choosing which tags they are offered, and reviewing what they marked.

**The client has their own instructions, inside the portal.** The last tab of
their menu, **How this works**, is a single page: what they can see, how marking
a transaction works, what a struck-through tag means, and how the amount search
reads a sign. You do not have to send them anything, and there is nothing for
them to lose.

It is written for them rather than for you — so when a client asks how
something works, point them at that tab. The source is
`docs/client-guide/README.md`; like this guide, the published copy is generated
and a test fails if it drifts.

> ⚠ **Tell the attorney this before the first client is let in: a client sees
> the whole matter.** That includes the other side's produced accounts and
> transactions. It is the intended behaviour, and it is the first question
> anybody asks.

---

## 1. Switch it on

Two separate things have to be true, and neither happens by itself.

**The account.** The person needs the `client` role and a client record
([Chapter 12](12-user-roles.md)). Until then they sign in and reach an
"access denied" page.

**The matter.** Go to the matter detail page → **Client portal** → tick **Let
the client tag transactions**.

| | |
| - | - |
| **Reading is always allowed** | A client with the role can read their matter. This tick box controls **tagging** only |
| **Tagging is off by default** | A matter does not acquire a tagging client by accident |
| **Turn it off before a hearing** | Tags reach exhibits, and the set should stop moving before anybody argues from it. The client sees a note saying tagging is paused, and **what they already marked is kept** |

---

## 2. Choose which tags they are offered

**Financials** → **Transactions** → **Manage tags**. Each tag has **Offer this
tag to the client in their portal**.

- **Nothing is offered until somebody ticks it.** Opt-in, because the firm's own
  tag list carries strategy — *"Waste: girlfriend"* is not a label to hand to
  the other spouse.
- Offered tags show a small **client** chip in the manager, so the published set
  is visible at a glance.
- **Seven client-facing waste tags ship with this release.** Rename or retire
  them like any other tag ([Chapter 9](09-tagging.md)).
- Clients cannot invent tags. They choose from what you publish.

---

## 3. Review what the client marked

**Financials** → **Transactions**, beneath the search panel.

Each row shows the transaction and the tag the client applied. Two actions:

| Action | What it does |
| ------ | ------------ |
| **Clear** | Suppresses the tag. The client sees it struck through and **cannot put it back** |
| **Restore** | Puts it back — and it becomes **the firm's tag**, because applying it is your judgment at that point |

**Why this screen exists at all: a client tag that reaches an exhibit is an
exhibit resting on a guess.** Tags drive Rule 1006 summaries
([Chapter 10](10-exports.md)), and a client's hunch arriving in one as though an
attorney had made it is the one outcome this has to prevent. So a client tag and
a staff tag are visibly different everywhere, and this is where they are told
apart.

**Work the list before building an exhibit from any tag a client can apply.**

---

## What the client sees

Four pages, and nothing else:

| Page | |
| ---- | - |
| **Accounts** | The accounts on the matter, with what is held for each |
| **Statements held** | Which months are on file, and which are missing |
| **Transactions** | Every line, searchable — including by amount, with the same sign rule staff use ([Chapter 8](08-categorizing-and-fis.md#finding-large-transactions)) |
| **How this works** | Their own one-page guide |

They cannot export, categorize, correct a line, add a line, or see the FIS,
the compliance exhibits, costs, billing, or any other matter.

---

[← AI costs and prices](15-costs.md) · [Index](README.md)

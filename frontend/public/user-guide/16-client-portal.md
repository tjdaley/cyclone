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

Two separate switches, and neither happens by itself.

### Give the client access

1. **Clients** → click the client to open the edit form.
2. Check **Login email (client portal)**. It must be the **Google address** they
   will sign in with. If you change it, click **Save changes** first.
3. In the **Client portal** panel, click **Give portal access** and confirm.
4. Tell the client to go to the Cyclone address, click **Log in** and use that
   Google account. Cyclone does not email them.

**A login email alone does nothing.** Every client record has one, so it cannot
mean "may sign in". Without **Give portal access** they sign in with Google and
are told no account is waiting for them.

The panel always shows where things stand:

| It says | Meaning |
| ------- | ------- |
| **No access** | They cannot get in |
| **Access given… not signed in yet** | They can get in, and have not yet |
| **Active** | They have signed in, and their Google account is linked |

**A client with several matters sees all of them**, and switches between them with
a list at the top of the page. Access is per client; you hide one matter with the
switch below.

**Some addresses are refused.** Each person needs their own Google address, so
Cyclone refuses one already used by another client, a staff member or a financial
professional. Spouses sharing one mailbox is the case to expect.

### Revoke it

Click **Revoke portal access** in the same panel and confirm. They lose access on
their next click, and anything they tagged stays on the transactions. You can give
access again later, and the same Google account links straight back.

Revoke first if you need to **change the login email** or **delete the client**.
Cyclone refuses both while access is in place.

### Hide one matter

Matter detail page → **Client portal** → untick **Show this matter in the client's
portal**. The client cannot see that matter at all, and everything they marked on
it is kept. Tick it again to bring it back.

**Closing a matter asks you this.** When you change a matter's status to `closed`
or `archived`, Cyclone asks whether to turn portal access to it off. Choose **Close
and turn it off**, **Close and leave it on**, or **Keep editing** to close nothing.
It asks whether or not the client has access today, because the answer still
applies if they are given access later.

Nothing turns back on when you reopen a matter, and a matter closed before this
question existed stays visible until somebody unticks it. **To give a client an
old case back** for a modification or an enforcement, tick the box on that matter
and make sure the client has access.

### Let them tag, on each matter

Go to the matter detail page → **Client portal** → tick **Let the client tag
transactions**.

| | |
| - | - |
| **Reading does not depend on it** | A client with access can read any matter that is showing. This tick box controls **tagging** only |
| **Tagging is off by default** | A matter does not acquire a tagging client by accident |
| **Turn it off before a hearing** | Tags reach exhibits, and the set should stop moving before anybody argues from it. The client sees a note saying tagging is paused, and **what they already marked is kept** |

---

## 2. Choose which tags they are offered

**Financials** → **Transactions** → **Manage tags**. Tick **Offer to clients**
when you add a tag, or later by clicking **Edit** on it.

- **Nothing is offered until somebody ticks it.** Opt-in, because the firm's own
  tag list carries strategy — *"Waste: girlfriend"* is not a label to hand to
  the other spouse.
- Offered tags show a small **client** chip in the manager, so the published set
  is visible at a glance.
- **Seven client-facing waste tags ship with this release.** Rename or retire
  them like any other tag ([Chapter 9](09-tagging.md)).
- Clients cannot invent tags. They choose from what you publish.
- **A client always keeps their own marks.** Stop offering or retire a tag, and
  they can no longer apply it, but they can still see and search for the lines
  they already marked with it. The same goes while tagging is paused.

---

## 3. Review what the client marked

**Financials** → **Transactions**. Work one tag at a time, because different tags
call for different research: "Ask me about this" is a different job from "Possible
waste".

1. In the **Tags** row, click the chip for a client tag. Its number is how many
   lines on this matter carry it.
2. A mark the client made reads **· client** on the line. A financial professional's
   tags never reach the client, not even as an unnamed mark.
3. To reject marks, tick the lines and use **Remove tag…**.

| Action | What it does |
| ------ | ------------ |
| **Remove tag…** on a client's mark | Suppresses the tag. The client sees it struck through and **cannot put it back** |
| **Add tag…** on a line where it was removed | Puts it back, and it becomes **the firm's tag**, because applying it is your judgment at that point |

**There is no list of what you removed.** The record is kept, and the client still
sees it struck through, but a removed mark does not appear on your screens.

**Why it matters: a client tag that reaches an exhibit is an exhibit resting on a
guess.** Tags drive Rule 1006 summaries ([Chapter 10](10-exports.md)), and a client's
hunch arriving in one as though an attorney had made it is the one outcome this has
to prevent.

**Work through a tag before building an exhibit from any tag a client can apply.**

---

## What the client sees

Four pages, and nothing else:

| Page | |
| ---- | - |
| **Transactions** | Every line, searchable — including by amount, with the same sign rule staff use ([Chapter 8](08-categorizing-and-fis.md#finding-large-transactions)) |
| **Accounts & statements** | The accounts on the matter, each statement held, and a link to open its PDF |
| **What is still needed** | Which months are on file, and which are missing |
| **How this works** | Their own one-page guide |

They cannot export, categorize, correct a line, add a line, or see the FIS,
costs, billing, or anybody else's matter.

---

[← AI costs and prices](15-costs.md) · [Index](README.md) · Next: [Letting a financial professional in →](18-financial-professionals.md)

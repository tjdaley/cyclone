# 14. Where leads go

[← Assigning staff to a matter](13-matter-staff.md) · [Index](README.md)

---

A lead does not arrive with a name on it. It arrives with a **slug** — a short
handle the marketing site attaches, saying which attorney's page or subdomain
the prospective client came in through. Everything about where that lead goes
follows from matching that slug to something inside Cyclone.

This chapter is for whoever administers Cyclone. It is the setup a new attorney
needs before their leads reach anybody, and it is the thing to read when a lead
came in and nobody was told.

---

## The three things that must line up

They are separate, and a lead can fail at any one of them while the other two
are fine.

| | What it decides | Where it is set |
| - | --------------- | --------------- |
| **The staff slug** | Which attorney the lead belongs to | Admin → the staff record → **Slug** |
| **Responders** | Which people are actually emailed when a lead needs attention | Admin → **Lead responders** |
| **Slug access** | Which people can see the lead on the Leads screen | Not on screen — see [Who can see a lead](#who-can-see-a-lead-is-a-separate-question) |

**The slug is the join.** The marketing site's slug and the staff record's
**Slug** field must match exactly — same spelling, same case, no stray spaces.
Nothing matches `jdoe` to `JDoe`.

---

## What happens when a lead arrives

1. **Cyclone welcomes them.** An acknowledgment email goes out to every lead
   that arrives with an email address. This happens regardless of everything
   below.
2. **It looks for an attorney.** A `staff` record whose **Slug** equals the
   lead's slug is the attorney for that lead, and the lead is assigned to them
   on the spot. No matching slug means the lead stays unassigned.
3. **It reads the message.** The lead's own description of their situation is
   classified: obvious spam is filed as disqualified, a message that needs a
   lawyer is escalated, and anything routine may get a draft reply prepared for
   a human to approve.
4. **Escalation notifies the responders.** Not the attorney — **the people
   listed as that attorney's responders**, by email to their work address, and
   by Telegram for anyone whose staff record carries a Telegram chat ID.

So an attorney with no responders is notified about nothing, and a lead with no
matching slug reaches step 4 with nobody to notify. Both failures are recorded
on the lead, and both are covered under
[When escalation fails](#when-escalation-fails).

---

## Leads with no attorney preference

Not every visitor arrives through an attorney's page. Those leads carry the
firm's own slug instead, and they need a **stand-in staff record** so escalation
has an attorney to resolve and responders to notify. Without one, every
general-inbox lead fails to escalate.

Three slugs are treated as "the firm's general inbox" and are deliberately
**never auto-assigned** to anybody, even when a staff record shares the slug:
`www`, `home`, and blank. Those leads sit unassigned in the list with an
**Assign to me** button, which is the intent — they are up for grabs. Escalation
still resolves them through the matching staff record, so the stand-in record is
required for these too.

**Any other firm-wide slug behaves like an attorney slug** and auto-assigns its
leads to the stand-in record. That is worth knowing before you see leads
apparently assigned to a person who does not exist.

### Setting up the stand-in record

1. **Admin** → **New staff member**.
2. **Slug**: exactly the slug the marketing site sends for firm-wide leads.
3. **Role**: `attorney`. Only attorney records can have responders.
4. **First** / **Last**: something that reads as what it is — `Firm` / `Intake`.
   It will appear in staff lists, so do not name it after a person.
5. **Leave the sign-in email blank.** No sign-in email means no login and no
   auth role, which is what you want: this is a routing record, not a person.
6. Save, then open **Lead responders**, click the new record, and tick everyone
   who should receive its escalations.

> **It will appear in staff pickers.** The assignee dropdown, matter staff, and
> similar lists include every staff record. There is no "inactive" flag to hide
> it behind. That is the cost of this arrangement, and it is a small one.

---

## Adding a new attorney

Creating the staff record ([Chapter 1](01-getting-started.md)) is half of it.
For leads to work, all of this has to happen:

| | |
| - | - |
| **1. Slug matches the marketing site** | On the staff record. Exactly, including case. |
| **2. Auth roles granted** | Admin → **User roles**. Without this they cannot sign in at all — see [Chapter 12](12-user-roles.md). |
| **3. Responders configured** | Admin → **Lead responders** → their row. An attorney cannot be their own responder, and only non-attorney staff can be picked. |
| **4. Slug access granted** | So they, and their responders, can open their own leads. See below. |

Step 4 is the one that gets missed.

---

## Who can see a lead is a separate question

Seeing leads is governed by a list of slug grants per staff member, and it has
**nothing to do** with who a lead is assigned to. A new attorney is auto-assigned
their own leads and still cannot open them until they are granted access to their
own slug.

What you get without doing anything:

- **Admins see every lead**, always.
- **Every newly created staff member is granted `www`** automatically, and
  nothing else. That is the general inbox, not their own slug.

Everything beyond that — an attorney seeing their own slug, a paralegal covering
a second attorney, the `*` grant that means "all slugs", or taking a grant away
— is stored in the `staff_slug_access` table and **has no screen yet**. An
administrator adds a row in the Supabase SQL editor:

```sql
-- Let staff member 7 see leads carrying the slug 'jdoe'
insert into staff_slug_access (staff_id, slug) values (7, 'jdoe');

-- '*' means every slug, present and future
insert into staff_slug_access (staff_id, slug) values (7, '*');

-- Take a grant away
delete from staff_slug_access where staff_id = 7 and slug = 'jdoe';
```

Someone with no grants at all sees an empty Leads list rather than an error. A
lead they cannot see answers **"No access to this lead"** if they reach it by
link.

---

## When escalation fails

Both failures are written to the lead's own timeline, under **Agent escalated**,
as an ordinary italic note. They are easy to miss — nothing turns red, and no
one is emailed about the failure to email anybody. If a lead came in and the
office was quiet, open the lead and read the timeline.

| What the note says | What it means | What to do |
| ------------------ | ------------- | ---------- |
| **"Escalation failed: no responding attorney could be resolved for this lead."** | The lead is unassigned *and* no staff record has a **Slug** matching the lead's slug. | Check the lead's slug — it is the grey chip beside the name on the lead page — against the staff records. Usually the slug is new, or is the firm-wide one and has no stand-in record yet. |
| **"Escalation failed: no responders configured for the responding attorney."** | The attorney was found. Nobody is listed to notify. | Admin → **Lead responders** → that attorney → tick people → **Save responders**. |

Two smaller things that produce the same silence:

- **A responder with no Telegram chat ID** is emailed only. That is normal, not
  a fault.
- **An escalation email that bounces** shows on the lead as a red
  **Email send failed** card. That is a different problem from the two above:
  the routing worked and the mail did not.

---

## Changing the setup does not re-run past leads

Each lead is processed once, when it arrives. Creating the missing staff record,
adding responders, or granting access fixes **the next** lead. It does not go
back and notify anybody about the ones that already failed.

So after a fix, work the backlog by hand: open the Leads list, switch the filter
to **all**, and deal with anything that came in while the routing was broken.

---

## Reference: how each slug behaves

| The lead's slug | Assigned automatically? | Escalation resolves through |
| --------------- | ----------------------- | --------------------------- |
| Matches an attorney's slug | Yes, to that attorney | That attorney's responders |
| `www`, `home`, or blank | **No** — left for someone to claim | The staff record with that slug, if one exists |
| Any other firm-wide slug | Yes, to the stand-in record | That record's responders |
| Matches nothing | No | **Nothing — escalation fails** |

---

[← Assigning staff to a matter](13-matter-staff.md) · [Index](README.md)

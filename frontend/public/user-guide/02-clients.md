# 2. Creating a client

[← Getting started](01-getting-started.md) · [Index](README.md) · Next: [Creating a matter →](03-matters.md)

---

A client is a person the firm represents. Every matter belongs to one.

There are four ways a client record comes into existence. Typing one in by
hand is the one you will use least often, so it is worth knowing all four
before you start typing.

| How | When it happens | Where |
| --- | --------------- | ----- |
| **By hand** | Someone walks in, or you are catching up on an existing case | **Clients** → **+ New client** |
| **From a lead** | The prospect came through the website or an email enquiry | **Leads** → open the lead → **Promote** |
| **From a filed pleading** | You have a petition and no file yet — Cyclone reads the caption and opens the client *and* the matter together | **Matters** → intake |
| **Automatically, during intake** | A pleading intake matched an existing client and reused it | — |

**Prefer promoting a lead over typing a new client.** A lead already carries
contact details and has been through the intake conversation; re-typing it by
hand loses that and risks a second record for the same person.

---

## Run the conflict check first

Conflict checking in Cyclone is a **search, not a gate**. Nothing stops you
creating a client who conflicts. Run it deliberately, before you create the
record.

1. Go to **Clients**.
2. In the **Conflict of interest check** panel, type the prospective client's
   name.
3. Optionally add opposing party names, comma-separated
   (`John Smith, ABC Corp`).
4. Click **Run conflict check**.

You get one of two answers:

- **No conflicts found** — a green bar. Proceed.
- **N potential conflicts — attorney review required** — a red panel listing
  each hit with the name, their role, and the matter caption they appear in.
  **Stop and take this to an attorney.** Do not create the client on your own
  judgment.

> **What the check actually searches.** It matches names against existing
> clients and opposing parties. It is a text match, so it finds close spellings
> but is not clever about nicknames or maiden names. A clean result means
> nothing similar was found — it is not a legal opinion, and your firm may well
> run a fuller check outside Cyclone.

---

## Creating the client

1. **Clients** → **+ New client**.
2. Fill in the form:

   | Field | Required | Notes |
   | ----- | -------- | ----- |
   | **First name** | Yes | |
   | **Last name** | Yes | |
   | **Login email (for client portal)** | Yes | The address the client would use to sign in. The client portal is not built yet, so nothing uses this today — but it is required, and it should be right. |
   | **Contact email** | Yes | Where you actually write to them. Often the same address. |
   | **Telephone** | Yes | |
   | **Referral type** | Yes | Chosen from a list your firm configures. |
   | **Referral source** | Yes | The referring attorney, client, or channel by name. |
   | **Referred to** | No | The attorney the referral was directed to. Leave as **Firm (general)** if it came to the firm rather than a person. |
   | **Prior counsel** | No | The lawyer who had the case before, if any. |
   | **Notes** | No | |

3. Click **Create client**.

The button stays disabled until every required field has something in it.

---

## Editing a client

Click any client row in the list to open it, then **Edit**. Beyond the creation
fields, an existing client also carries:

| Field | What it is |
| ----- | ---------- |
| **Status** | `prospect`, `pending conflict check`, `conflict flagged`, `active`, `inactive`. Set it deliberately — nothing sets it for you. |
| **Ending A/R balance** | What they owed at the end of a prior engagement. |
| **OK to rehire** | A tick box. Your firm's own answer to whether you would take them back. |

Use the **Search clients…** box to filter the list.

---

## What comes next

A client on its own does nothing. The work happens on a **matter**, which is
the next chapter. One client can have several matters — a divorce and a later
modification are two matters for one person.

---

[← Getting started](01-getting-started.md) · [Index](README.md) · Next: [Creating a matter →](03-matters.md)

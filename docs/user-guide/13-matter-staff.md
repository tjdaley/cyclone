# 13. Assigning staff to a matter

[← User roles](12-user-roles.md) · [Index](README.md)

---

## Read this first

**Assigning staff to a matter does not control who can see it.**

Cyclone has no per-matter access control. Anyone holding the attorney,
paralegal, or admin role can open **every** matter in the firm, and everything
in it — clients, statements, transactions, exhibits — whether or not they are
assigned to it. Removing someone from a matter takes nothing away from them.

What the Staff section actually records is **who is working on the case and who
gets origination credit for it**. Both are useful. Neither is a security
control, and it is worth being clear about that before anyone relies on it to
keep a case confidential.

If a matter must genuinely be walled off from some staff, Cyclone cannot do that
today. Raise it rather than assuming an assignment covers it.

---

## Assigning someone

**Matters** → the matter → the **Staff** section → **+ Assign staff**.

1. Choose the staff member.
2. Choose their role on the matter:

   | Role | Meaning |
   | ---- | ------- |
   | **originating** | Brought the business in. Carries an origination credit percentage. |
   | **billing reviewer** | Reviews the bill before it goes out. |
   | **assigned** | Working the case. |

3. For **originating** only, enter a **Split %**. The field is disabled for the
   other roles.
4. Click **Assign**.

---

## Origination credit

More than one person can be originating, each with a share.

Beneath the list, a meter shows the running total: **"Origination — 60% of
100%"**. It turns red past 100%.

> **The database will refuse a write that pushes the total over 100%.** You will
> get an error saying the percentages for this matter would exceed 100%. Reduce
> someone else's share first, then add the new one.

---

## Changing an assignment

Everything is editable in place in the list:

- **Role** — the dropdown beside each name. Changing away from *originating*
  removes the split.
- **Split %** — type over it; it saves when you click away.
- **Remove** — takes the person off the matter (and does not change what they
  can see).

---

## What assignment is actually used for

| Purpose | How |
| ------- | --- |
| **Knowing who is on the case** | The Staff section is the record |
| **Origination credit** | Reported per staff member, capped at 100% per matter |
| **Billing review** | The billing reviewer role marks who signs off |

Billing **rates** are not set here. Those are on the matter's **Rate
overrides**, and only an admin can set them — see
[Chapter 3](03-matters.md).

---

## Related things that *are* access controls

| Question | Answer |
| -------- | ------ |
| Can this person use Cyclone at all? | Their **auth roles** ([Chapter 12](12-user-roles.md)) |
| Can this person create matters, or only work them? | attorney/admin create; paralegals work |
| Can this person change firm-wide settings? | admin, and attorneys for the category chart |
| Can this person see *this particular* matter? | **Yes, if they can use Cyclone at all.** There is no per-matter control |

---

[← User roles](12-user-roles.md) · [Index](README.md)

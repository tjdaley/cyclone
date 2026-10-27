# Discovery Document Pipeline: Specification

**Owner:** Tom Daley
**Status:** Design agreed; implementation starting
**Companion files:** `discovery_classifier_schema.json` (classifier taxonomy and questions), `production_index_schema.sql` (production index data model)

---

## 1. Purpose

Replace the current single large prompt that classifies and extracts bank, credit card, and brokerage statements with a pipeline that:

1. Inventories a whole folder of discovery documents by type.
2. Routes each document to a type-specific processor.
3. Maps documents to the discovery requests they respond to.
4. Produces a cumulative **production index** served as an exhibit with each production response.
5. Runs the same analysis over the other side's production to find gaps.

The current prompt works but is fragile: one change to handle a new document type risks breaking the others. Separating classification from extraction isolates those failures.

## 2. Context and constraints

- **Source documents** are mostly native-text PDFs. The mix varies by client and by look-back window; older periods have more scans and odd layouts.
- **One document per file.** Productions usually arrive already split into separate PDFs, and upstream processes are being put in place to make that the rule. The pipeline classifies per file and does not split pages. It still flags files that appear to contain more than one document (see §4.3).
- **Confidentiality.** Inputs are client financial records, medical and mental health records, and records concerning children. Any third-party model API must be cleared for this data (retention and training terms) before use. The design must allow a locally hosted model to be swapped in.
- **Legal use.** The production index is served over counsel's signature. Nothing the models produce reaches an exhibit without human approval.

## 3. Architecture

```
folder of PDFs
   │
   ▼
[1] Text-layer check (deterministic, no model)
   │            │
   │ pass       │ fail (scan, photo, screenshot, garbled)
   ▼            ▼
[2] Decision-model classifier      [2b] Vision-capable LLM classifier
   │     │                                │
   │     │ below confidence threshold     │
   │     └──────────────┬─────────────────┘
   │                    ▼
   │             [3] Human review queue
   ▼
[4] Router → type-specific processors (bank, credit card, brokerage, tax return, ...)
   │
   ▼
[5] Responsiveness mapping (rules first, then model) → proposed mappings
   │
   ▼
[6] Human approval → production index exhibit
```

Each stage writes its result to the database so any stage can be re-run without repeating the others.

## 4. Stage specifications

### 4.1 Text-layer check

- Extract text per page from the PDF text layer.
- Compute characters per page and the ratio of non-alphanumeric or replacement characters.
- Fail the file if it falls below a minimum character count or above a maximum garbage ratio. **Both thresholds are configurable**; set starting values, then tune them against the labeled test set (§7).
- Record the metrics on the document row so failures can be reviewed.

**Why:** It is free, deterministic, and keeps unreadable text from reaching a classifier that will confidently mislabel it.

### 4.2 Classifier input

- Send the extracted text of the **first one or two pages** only, not the whole document.
- Use the instruction, options, and descriptions in `discovery_classifier_schema.json`.

**Why:** Document type is almost always evident from the header and first page. Sending less is faster, cheaper, and reduces exposure of sensitive content.

### 4.3 Classifier output

One call per file returns:

- `document_type`: one choice from the taxonomy, with a probability.
- Companion yes/no questions: `multiple_documents`, `multiple_accounts`, `transaction_detail_present`, `business_or_entity_owner`, `annual_or_year_end`, `attorney_involved`, `involves_child`, `non_english`.
- `text_quality`: a 0–100 score.

Handling rules:

| Condition | Action |
|---|---|
| `document_type` probability below threshold | Human review queue |
| `document_type` = `other_unknown` | Human review queue |
| `multiple_documents` = yes | Send back upstream for splitting; do not process |
| `attorney_involved` = yes | Flag for privilege review before production. This is a tripwire, not a privilege determination. |
| Otherwise | Route to the processor for that type |

**Why the companion questions:** They cost nothing extra in the same call and sharpen routing (e.g., multi-account statements need different extraction). `multiple_documents` also gives a running measure of how well upstream splitting is working.

### 4.4 Taxonomy design rules

- **Classes follow processing branches, not the document's nature.** If two document kinds go to different processors, they are separate classes. If they share a processor, merge them and use a sub-type question.
- **Every option has a one-line description.** These models classify against the option text supplied; a description beats a bare label, especially on look-alikes.
- **`other_unknown` is mandatory.** Without it the model forces every oddball into a real category.
- **Internal labels are not exhibit labels.** Map to display names through `doc_type_display`.

Specific calls in the current taxonomy, each open to revision if the processors say otherwise:

- Retirement statements are separate from brokerage statements; loan statements are separate from credit card statements.
- Mental health records are separate from medical records because they need different handling.
- Co-parenting app exports (OurFamilyWizard, AppClose, TalkingParents) are separate from text messages because their structure is worth parsing on its own.
- Inventory and appraisement has its own class rather than falling under court filings.

**Fallback if accuracy is poor:** 38 options in one choice question is a lot. If testing shows confusion between look-alike pairs (brokerage vs. retirement, invoice vs. receipt, letter vs. email), switch to two stages: classify into roughly eight families, then the specific type within the family.

### 4.5 Model adapter

- Put the classifier behind a vendor-neutral interface: input text plus the schema in, typed answers with probabilities out.
- Implement adapters for each candidate in the bake-off (§7.3). Field names differ between vendors; map them in the adapter, never in the schema file.

**Why:** No decision model is a clear winner yet, rankings flip depending on who runs the benchmark, and the confidentiality review may rule out hosted options. Swapping models must not touch the rest of the pipeline.

### 4.6 Responsiveness mapping

Run in two passes, in this order:

1. **Rules (label + metadata).** Most requests for production are category + party + time period ("all statements for any account in your name since January 2022"). Match on `doc_type`, account holder, and `period_begin`/`period_end` from the extractors. Source = `rule`.
2. **Model (document text).** For topical requests ("all communications concerning the sale of the Frisco house"), score the document text against the request. Send the **whole document**, chunked if long; any responsive chunk makes the document responsive. Only test requests the label filter did not already rule out. Source = `model`.

Rules:

- **Score against `operative_text`, never the raw request.** `operative_text` is a self-contained restatement with definitions, instructions, the relevant time period, and any narrowing by objection, agreement, or order folded in. The model sees only what it is given.
- **Store the probability, not a yes/no.** Threshold by which error is costlier. When checking our own production, a missed responsive document is the costly error, so set the threshold low and review more.
- **Many-to-many.** One document may respond to several requests. Ask all surviving requests as separate yes/no questions in one call.
- **No rationale comes back.** Decision models return a number, not a reason. Every mapping is `proposed` until a person approves it.

**Why label first:** It answers the common requests deterministically with an audit trail, and it prunes the work so a request for tax returns is never tested against text messages.

### 4.7 Production index

Data model is in `production_index_schema.sql`. Key decisions:

| Decision | Reason |
|---|---|
| Two tables: `document` (one row per document) and `document_request` (one row per document–request pair); the exhibit is a view over the join | A document's details are stored once and cannot drift between its duplicate exhibit rows |
| Requests keyed on request set + number | "First RFP No. 12" and "Second RFP No. 12" are different requests |
| Exhibit shows a **description** ("Chase checking x1234, Jan. 2023"), not the file name | More useful to a judge; internal and client file names can carry commentary that must not be served |
| Only `approved` mappings appear in the exhibit | The index is a representation over counsel's signature |
| `source` and reviewer recorded on every mapping | Audit trail for how each mapping was made and who approved it |
| Index is **cumulative**; each supplement re-serves the full index | The latest exhibit is always complete |
| Withheld documents never appear | They belong in the withholding statement and privilege log |
| `producing_party` on each production | The same tables index our production and audit theirs |
| `period_begin` / `period_end` on statements | Enables coverage and gap analysis (§4.8) |

**Exhibit layout**

Title: *Exhibit A: Cumulative Production Index, [Party]'s Responses to [Requesting Party]'s Requests for Production, current through [date]*

Columns, sorted by request set, then request number, then Bates:

1. Request set
2. Request no.
3. Bates range
4. Document type
5. Description
6. Document date
7. Date produced
8. Production volume

**Response language (template text)**

Per request, replacing the Bates-range sentence:

> Responsive documents have been produced. See the Production Index attached as Exhibit A, which identifies by Bates number the documents produced in response to this Request.

Once, in the preliminary statement:

> The Production Index attached as Exhibit A is provided under Rule 196.3(c) to organize and label the documents produced to correspond with the Requests. It is cumulative and supersedes any prior index. It is provided as a convenience and is not a limitation on the production: a document may be responsive to Requests in addition to those listed for it, and the omission of a Request number beside a document is not a representation that the document is unresponsive to that Request. Documents withheld under a claim of privilege are not listed and are addressed in the withholding statement served under Rule 193.3.

Store these as editable templates; counsel adjusts wording per case.

### 4.8 Auditing the other side's production

- Load their production with `producing_party` set to the other side and run stages 1–5 the same way.
- **Coverage report:** for each request, list which accounts and which months are present, and flag missing months within the requested period. This comes from labels and extracted metadata, not from the responsiveness model.
- **`requests_without_documents`** on their side is the starting list for a deficiency letter.

**Why coverage over responsiveness:** Responsiveness scoring shows what was produced. Deficiency is mostly about what was not, and gaps in statement periods are the clearest evidence of it.

## 5. QC views

Already defined in the SQL:

- `unmapped_documents`: produced documents tied to no approved request. Must be empty, or explained, before an index is served.
- `requests_without_documents`: on our side, confirm the written response says nothing was produced or states an objection; on theirs, see §4.8.

## 6. Human review

Build a review screen that shows, per document: the label and probability, the companion flags, the proposed request mappings with source and probability, and the PDF. Reviewers approve, reject, or add mappings and correct labels. Corrections are stored and feed back into the labeled test set.

## 7. Evaluation

### 7.1 Labeled test set

- A few hundred hand-labeled documents.
- **Sample across clients and across look-back windows**, not from one matter.
- **Over-sample older periods**, where scans and unusual layouts concentrate.
- Include deliberate `other_unknown` examples and look-alike pairs.

### 7.2 Metrics

- Accuracy and confusion matrix per class, with attention to look-alike pairs.
- Calibration: does a 90% probability hold up about nine times in ten? Check this directly; one independent benchmark found a vendor's confidence field added little beyond its top probability.
- Review rate at candidate thresholds (how much goes to humans).
- Latency and cost per document.

### 7.3 Bake-off candidates

Run on the identical test set:

1. **Microsoft-Decision-1** via Microsoft Foundry (available now, enterprise terms).
2. **One open-weight decision model run locally** (no third-party data exposure).
3. **The existing large prompt** as the baseline.

If the baseline wins by a wide margin, the cost savings may not justify switching: folder-sized jobs are small enough that per-document LLM cost is rarely the constraint.

**Why test locally rather than trust benchmarks:** Published results vary more by task than by model (one independent study saw the same model range from about 63% to 95% across tasks). Classifying a bank statement against a drug test result is an easier task than most benchmark workflows.

## 8. Open questions

- Which model wins the bake-off, and whether the hosted candidates clear the confidentiality review.
- Starting thresholds for the text-layer check, classifier confidence, and responsiveness.
- Whether the taxonomy needs the two-stage fallback.
- Exhibit output format (Word, Excel, PDF) and whether it follows the firm's brand template.
- Whether file names are ever clean enough to show in the exhibit.

## 9. Out of scope for this phase

- Page-level splitting of combined PDFs (handled upstream).
- Privilege determinations (the pipeline only flags).
- Automatic service of anything.

/**
 * Types for the splitting bench — one received PDF becomes N documents.
 *
 * Mirrors app/schemas/bench.py.
 */

/** One page of the grid. */
export interface BenchPage {
  page: number
  chars: number
  /** Pagination the page prints, e.g. "Page 1 of 4". A fact, not a boundary guess. */
  page_of: string | null
  /** A date printed in the page's header, ISO. */
  date: string | null
  /** A digit group the header labels as an account number. */
  account: string | null
  snippet: string | null
  /** Nothing to read: a separator sheet or a blank back. */
  blank: boolean
  /** Has text but not language — a bad scan. Different fact from blank. */
  unreadable: boolean
  /** The document already holding this page, if one does. */
  claimed_by_document_id: number | null
}

/** One document already split off this file, and the pages it holds. */
export interface BundleSummary {
  document_id: number
  /** Normally null — the bench does not classify. */
  doc_type: string | null
  /** Page ranges in the source, e.g. ["10-12", "15"]. */
  ranges: string[]
  /** Every page it holds, so the grid can mark them. */
  pages: number[]
  page_count: number
  /** False once something downstream points at it. */
  can_undo: boolean
  /** The statement read out of this document, once one has been. */
  statement_id?: number | null
  /**
   * What that statement IS — institution, account and period. A row id
   * correlates to nothing a person can see.
   */
  statement_label?: string | null
}

/**
 * What the operator is in for, reported before they start.
 *
 * An oversized pile routes here instead of being refused, so stating the size
 * is what makes cancelling a real choice rather than a trap.
 */
export interface BenchSession {
  source_document_id: number
  matter_id: number
  original_filename: string
  /**
   * The file has been read, so the grid can be shown. While this is false every
   * count below is zero — and zero pages must NOT read as "nothing left to do",
   * which is exactly what an empty grid said.
   */
  surveyed: boolean
  page_count: number
  documents_so_far: number
  pages_unclaimed: number
  readable_pages: number
  /**
   * Somebody ELSE holds this pile. Re-entry by the same person is not a
   * conflict — getting that wrong made the warning fire on every bundle.
   */
  held_by_other: boolean
  /** What has already been split off. Server state, not remembered here. */
  bundles: BundleSummary[]
}

export interface BenchGrid {
  source_document_id: number
  /** Pages in the whole file. */
  page_count: number
  /** Pages in the list being paged over — unclaimed only, unless asked for all. */
  pages_shown: number
  offset: number
  pages: BenchPage[]
}

export interface BundleResult {
  document_id: number
  ranges: string[]
  page_count: number
  storage_path: string | null
  pages_unclaimed: number
}

export interface FinishResult {
  source_document_id: number
  documents: number
  pages_swept: number
  swept_document_id: number | null
  /** The swept pages were all blank or unreadable, so the label is true of them. */
  swept_as_separator: boolean
}

/** What was recorded on upload, and what should happen to it next. */
export interface ReceivedUpload {
  source_document_id: number
  matter_id: number
  original_filename: string
  byte_count: number
  /** These exact bytes were already on the matter, so nothing was stored again. */
  already_held: boolean
  /**
   * 'ocr_first' | 'bench' | 'automatic', or null while the survey that reads
   * the file is still queued — deciding means reading every page, which is a
   * download the upload request must not wait for.
   */
  route: string | null
  /** Why, in words somebody can disagree with. */
  reason: string
  pages: number
  text_share: number
  /**
   * Share of pages inside a complete "Page i of N" run — the predictor of
   * whether an automatic split can work. Measured at 74% on a pile that splits
   * cleanly and 8% on one that cannot, both with a text layer, which is why
   * text quality is not the test.
   */
  pagination_coverage: number
  /** Documents that paginate themselves. A lower bound: a receipt prints none. */
  likely_documents: number
}

/** One file on record, and how far it has been split. */
export interface ReceivedDocument {
  source_document_id: number
  matter_id: number
  original_filename: string
  byte_count: number
  page_count: number | null
  received_on: string
  documents: number
  /** The file has been read, so its routing verdict is known. */
  surveyed: boolean
}

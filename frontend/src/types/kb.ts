/** Mirrors KbArticleResponse in app/schemas/kb_article.py */
export interface KbArticle {
  id: number
  topic: string
  subtopic: string | null
  body_md: string
  active: boolean
  sort_order: number
  created_at: string
  updated_at: string | null
}

/** Mirrors KbArticleCreateRequest in app/schemas/kb_article.py */
export interface KbArticleCreatePayload {
  topic: string
  subtopic?: string | null
  body_md: string
  active?: boolean
  sort_order?: number
}

/** Mirrors KbArticleUpdateRequest in app/schemas/kb_article.py */
export interface KbArticleUpdatePayload {
  topic?: string
  subtopic?: string | null
  body_md?: string
  active?: boolean
  sort_order?: number
}

import { createElement, isValidElement, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { guideHref, headingId } from '../lib/userGuide'

/**
 * The user guide's markdown, set for reading rather than for scanning a file.
 *
 * **`remark-breaks` is deliberately NOT used here**, though it is used on
 * discovery responses elsewhere in this app. The guide's prose is hard-wrapped
 * at about 78 characters so it reads in an editor and diffs cleanly; treating
 * every wrapped line as a line break would put a ragged edge down every
 * paragraph. Markdown's own rule — a blank line starts a paragraph — is what
 * the guide was written against.
 *
 * Raw HTML is not rendered (no `rehype-raw`), so the markdown cannot introduce
 * markup of its own. That matters more than usual here: this page is public.
 */

/** The text inside a heading, for its anchor id. */
function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (isValidElement(node)) {
    return textOf((node.props as { children?: ReactNode }).children)
  }
  return ''
}

/**
 * A heading that can be linked to.
 *
 * The guide cross-references its own sections by fragment
 * (`07-viewing-statements.md#correcting-a-line`), so those links only work if
 * the ids match what GitHub would have generated. `headingId` is that rule.
 */
function heading(tag: 'h1' | 'h2' | 'h3' | 'h4', className: string) {
  return function Heading({ children }: { children?: ReactNode }) {
    const id = headingId(textOf(children))
    return createElement(
      tag,
      { id, className: `group scroll-mt-24 ${className}` },
      children,
      // A quiet anchor: invisible until the heading is hovered, so nobody
      // reading the page has to look at it, and anybody who needs to cite a
      // section can get the URL without guessing the slug.
      createElement(
        'a',
        {
          href: `#${id}`,
          'aria-label': 'Link to this section',
          className: 'ml-2 text-gold opacity-0 group-hover:opacity-100 '
            + 'transition-opacity no-underline font-normal',
        },
        '#',
      ),
    )
  }
}

const components: Components = {
  h1: heading('h1', 'font-display text-3xl sm:text-4xl text-navy mt-12 mb-4 leading-tight'),
  h2: heading('h2', 'font-display text-2xl text-navy mt-12 mb-3 pb-2 border-b border-border'),
  h3: heading('h3', 'font-sans font-semibold text-lg text-navy mt-8 mb-2'),
  h4: heading('h4', 'font-sans font-semibold text-base text-text-primary mt-6 mb-2'),

  p: ({ children }) => (
    <p className="my-4 leading-7 text-text-primary">{children}</p>
  ),

  // In-app links stay in the SPA; a rewritten `.md` link becomes a route.
  a: ({ href, children }) => {
    const target = guideHref(href ?? '')
    if (target.startsWith('/')) {
      return (
        <Link to={target}
          className="text-navy underline decoration-gold decoration-2 underline-offset-2
                     hover:text-navy-light hover:decoration-navy transition-colors">
          {children}
        </Link>
      )
    }
    return (
      <a href={target}
        {...(/^[a-z]+:/i.test(target) ? { target: '_blank', rel: 'noreferrer' } : {})}
        className="text-navy underline decoration-gold decoration-2 underline-offset-2
                   hover:text-navy-light hover:decoration-navy transition-colors">
        {children}
      </a>
    )
  },

  strong: ({ children }) => (
    <strong className="font-semibold text-navy">{children}</strong>
  ),

  // The guide's callouts are all blockquotes, and several are warnings that
  // have to be impossible to skim past. A gold rule and a raised ground give
  // them the weight the prose assumes they have.
  blockquote: ({ children }) => (
    <blockquote className="my-6 border-l-4 border-gold bg-surface-raised
                           rounded-r-md px-5 py-1 text-text-primary
                           [&>p]:my-3 [&>p]:leading-7">
      {children}
    </blockquote>
  ),

  ul: ({ children, ...props }) => {
    // A GFM task list ("- [ ] Both look-back dates set") is a checklist to work
    // down, not a bulleted list. Its markers are rendered checkboxes, so the
    // disc would sit beside them.
    const isTaskList = (props as { className?: string }).className?.includes('contains-task-list')
    return (
      <ul className={isTaskList
        ? 'my-4 space-y-2 list-none pl-1'
        : 'my-4 space-y-2 list-disc pl-6 marker:text-gold'}>
        {children}
      </ul>
    )
  },
  ol: ({ children }) => (
    <ol className="my-4 space-y-2 list-decimal pl-6 marker:text-navy marker:font-semibold">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-7 [&>p]:my-2">{children}</li>,
  input: ({ checked, type }) => (
    type === 'checkbox'
      ? <input type="checkbox" checked={checked} readOnly
          className="mr-2 -mb-0.5 h-4 w-4 rounded border-border accent-navy" />
      : null
  ),

  // Nearly every chapter carries a reference table, and several are wider than
  // a phone. The wrapper scrolls rather than letting the page do it.
  table: ({ children }) => (
    <div className="my-6 -mx-4 sm:mx-0 overflow-x-auto">
      <table className="w-full min-w-[32rem] text-sm border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-navy text-white">{children}</thead>,
  th: ({ children, style }) => (
    <th style={style}
      className="text-left font-semibold px-4 py-2.5 first:rounded-tl-md last:rounded-tr-md">
      {children}
    </th>
  ),
  tr: ({ children }) => (
    <tr className="border-b border-border last:border-0 even:bg-off-white/60 align-top">
      {children}
    </tr>
  ),
  td: ({ children, style }) => (
    <td style={style} className="px-4 py-2.5 leading-6">{children}</td>
  ),

  code: ({ children, className }) => {
    // Fenced blocks arrive with a language class; inline code does not.
    if (className?.startsWith('language-')) {
      return <code className="font-mono text-[13px] leading-6">{children}</code>
    }
    return (
      <code className="font-mono text-[0.85em] bg-surface-raised text-navy
                       rounded px-1.5 py-0.5">
        {children}
      </code>
    )
  },
  pre: ({ children }) => (
    <pre className="my-6 overflow-x-auto rounded-lg bg-navy text-off-white
                    px-5 py-4 text-[13px] leading-6">
      {children}
    </pre>
  ),

  hr: () => <hr className="my-10 border-0 border-t border-border" />,
}

export default function GuideMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="font-sans text-[15px]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

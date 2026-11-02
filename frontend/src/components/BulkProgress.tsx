/**
 * Says that a bulk change is still being applied, beside the controls that
 * started it. A disabled dropdown looks exactly like one nobody touched, and
 * the natural response to a screen that has not moved is to do it again.
 */
export default function BulkProgress({ message }: { message: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm font-medium text-navy">
      <span aria-hidden="true"
        className="animate-spin w-4 h-4 border-2 border-navy/20 border-t-navy rounded-full" />
      {message}
    </span>
  )
}

/** "1 line" or "57 lines", for the messages around a bulk change. */
export function lineCount(count: number): string {
  return `${count.toLocaleString()} line${count === 1 ? '' : 's'}`
}

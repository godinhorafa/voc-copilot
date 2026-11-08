// Split an answer into text and citation markers, so [S3] and [M1] can be
// rendered as interactive chips. Grouped markers like "[S1, S3]" are handled;
// references the response doesn't contain stay as plain text.

export type Segment =
  | { kind: 'text'; text: string }
  | { kind: 'source'; number: number }
  | { kind: 'metric'; number: number }

const MARKER = /\[((?:[SM]\d+)(?:\s*,\s*[SM]\d+)*)\]/g

export function splitAnswer(
  answer: string,
  known: { sources: Set<number>; metrics: Set<number> },
): Segment[] {
  const segments: Segment[] = []
  let last = 0
  for (const match of answer.matchAll(MARKER)) {
    const refs = match[1].split(',').map((ref) => ref.trim())
    const resolved: Segment[] = []
    for (const ref of refs) {
      const number = Number(ref.slice(1))
      if (ref[0] === 'S' && known.sources.has(number)) resolved.push({ kind: 'source', number })
      else if (ref[0] === 'M' && known.metrics.has(number)) resolved.push({ kind: 'metric', number })
    }
    if (resolved.length !== refs.length) continue // leave unknown markers as text
    if (match.index > last) segments.push({ kind: 'text', text: answer.slice(last, match.index) })
    segments.push(...resolved)
    last = match.index + match[0].length
  }
  if (last < answer.length) segments.push({ kind: 'text', text: answer.slice(last) })
  return segments
}

export type Block = { kind: 'paragraph'; text: string } | { kind: 'list'; items: string[] }

const BULLET = /^\s*[-*•]\s+/

/** Light formatting: paragraphs, and runs of "- " / "* " lines as bullet lists,
 *  including a list that follows an intro line in the same paragraph. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = []
  for (const chunk of text.trim().split(/\n{2,}/)) {
    let paragraph: string[] = []
    let items: string[] = []
    const flushParagraph = () => {
      if (paragraph.length) blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
      paragraph = []
    }
    const flushList = () => {
      if (items.length) blocks.push({ kind: 'list', items })
      items = []
    }
    for (const line of chunk.split('\n')) {
      if (BULLET.test(line)) {
        flushParagraph()
        items.push(line.replace(BULLET, ''))
      } else if (line.trim()) {
        flushList()
        paragraph.push(line.trim())
      }
    }
    flushParagraph()
    flushList()
  }
  return blocks
}

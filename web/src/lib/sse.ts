// Server-sent events over fetch: EventSource can't POST, so /v1/ask?stream=true
// is read as a byte stream and split into events here.

export interface SSEMessage {
  event: string
  data: string
}

/** Parse one event block ("event: x\ndata: {...}"); null for comments or empty blocks. */
export function parseBlock(block: string): SSEMessage | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  return data.length ? { event, data: data.join('\n') } : null
}

/** Yield events as they arrive, handling blocks split across network chunks. */
export async function* readSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<SSEMessage> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    // stream: true keeps multi-byte characters split across chunks intact.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const message = parseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (message) yield message
      boundary = buffer.indexOf('\n\n')
    }
  }
  const last = parseBlock(buffer.trim())
  if (last) yield last
}

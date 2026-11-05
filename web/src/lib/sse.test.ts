import { describe, expect, it } from 'vitest'

import { parseBlock, readSSE } from './sse'

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const bytes = chunks.map((c) => new TextEncoder().encode(c))
  return new ReadableStream({
    start(controller) {
      for (const b of bytes) controller.enqueue(b)
      controller.close()
    },
  })
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const out = []
  for await (const message of readSSE(stream)) out.push(message)
  return out
}

describe('parseBlock', () => {
  it('reads event name and data', () => {
    expect(parseBlock('event: answer\ndata: {"a":1}')).toEqual({ event: 'answer', data: '{"a":1}' })
  })

  it('joins multi-line data and ignores comments', () => {
    expect(parseBlock(': keep-alive\ndata: a\ndata: b')).toEqual({ event: 'message', data: 'a\nb' })
  })

  it('returns null without data', () => {
    expect(parseBlock(': ping')).toBeNull()
  })
})

describe('readSSE', () => {
  it('handles events split across chunks', async () => {
    const events = await collect(
      streamOf('event: tool_start\nda', 'ta: {"x":1}\n\nevent: answer\n', 'data: {"y":2}\n\n'),
    )
    expect(events).toEqual([
      { event: 'tool_start', data: '{"x":1}' },
      { event: 'answer', data: '{"y":2}' },
    ])
  })

  it('keeps multi-byte characters split across chunks', async () => {
    const bytes = new TextEncoder().encode('data: “café”\n\n')
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 8)) // splits the opening quote mid-character
        controller.enqueue(bytes.slice(8))
        controller.close()
      },
    })
    expect(await collect(stream)).toEqual([{ event: 'message', data: '“café”' }])
  })

  it('normalizes CRLF and flushes a final event without a blank line', async () => {
    expect(await collect(streamOf('event: a\r\ndata: 1\r\n\r\nevent: b\ndata: 2'))).toEqual([
      { event: 'a', data: '1' },
      { event: 'b', data: '2' },
    ])
  })
})

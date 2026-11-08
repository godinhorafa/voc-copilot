import { describe, expect, it } from 'vitest'

import { splitAnswer, toBlocks } from './citations'

const known = { sources: new Set([1, 3]), metrics: new Set([1]) }

describe('splitAnswer', () => {
  it('turns markers into source and metric segments', () => {
    expect(splitAnswer('Churn tripled [M1] after the price change [S1].', known)).toEqual([
      { kind: 'text', text: 'Churn tripled ' },
      { kind: 'metric', number: 1 },
      { kind: 'text', text: ' after the price change ' },
      { kind: 'source', number: 1 },
      { kind: 'text', text: '.' },
    ])
  })

  it('handles adjacent and grouped markers', () => {
    expect(splitAnswer('a [S1][S3] b [S1, S3]', known)).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'source', number: 1 },
      { kind: 'source', number: 3 },
      { kind: 'text', text: ' b ' },
      { kind: 'source', number: 1 },
      { kind: 'source', number: 3 },
    ])
  })

  it('leaves unknown references as text', () => {
    expect(splitAnswer('see [S9] and [M2] and [S1, S9]', known)).toEqual([
      { kind: 'text', text: 'see [S9] and [M2] and [S1, S9]' },
    ])
  })

  it('ignores bracketed text that is not a marker', () => {
    expect(splitAnswer('[note] plain', known)).toEqual([{ kind: 'text', text: '[note] plain' }])
  })
})

describe('toBlocks', () => {
  it('splits an intro line from the list that follows it', () => {
    expect(toBlocks('Reasons:\n- price [S1]\n- outage\n\nThat is all.')).toEqual([
      { kind: 'paragraph', text: 'Reasons:' },
      { kind: 'list', items: ['price [S1]', 'outage'] },
      { kind: 'paragraph', text: 'That is all.' },
    ])
  })

  it('joins wrapped paragraph lines and accepts * and • bullets', () => {
    expect(toBlocks('one\ntwo\n* a\n• b')).toEqual([
      { kind: 'paragraph', text: 'one two' },
      { kind: 'list', items: ['a', 'b'] },
    ])
  })
})

import { describe, expect, it } from 'vitest'
import type { Event, Message } from '../types'
import { feedReducer, initialFeedState } from './feedReducer'

function createMessage(feedOrdinal: number, text: string = `msg-${feedOrdinal}`): Message {
  return {
    id: `msg:${feedOrdinal}`,
    sender_id: 'sender_test',
    sender_name: 'Test Sender',
    text,
    occurred_at: '2026-09-23T12:00:00Z',
    feed_ordinal: feedOrdinal,
    attachments: [],
  }
}

function createEvent(id: number, feedOrdinal: number): Event {
  return {
    id,
    feed_ordinal: feedOrdinal,
    message: createMessage(feedOrdinal),
  }
}

describe('FeedReducer & Gap Repair State Machine (Z9h / Z9i)', () => {
  it('initializes messages in sorted order and tracks nextExpectedOrdinal', () => {
    const raw: Message[] = [
      createMessage(1),
      createMessage(0),
      createMessage(2),
    ]

    const state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: raw,
    })

    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([0, 1, 2])
    expect(state.nextExpectedOrdinal).toBe(3)
    expect(state.buffer).toEqual({})
  })

  it('appends consecutive SSE events normally', () => {
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(0)],
    })

    expect(state.nextExpectedOrdinal).toBe(1)

    // Receive event with feed_ordinal = 1
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(101, 1),
    })

    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([0, 1])
    expect(state.nextExpectedOrdinal).toBe(2)
    expect(state.buffer).toEqual({})
  })

  it('ignores duplicate or already rendered events', () => {
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(0), createMessage(1)],
    })

    // Replay of event 1
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(101, 1),
    })

    expect(state.messages.length).toBe(2)
    expect(state.nextExpectedOrdinal).toBe(2)
  })

  it('Z9h: buffers higher feed_ordinal and refuses to render out of order when delivery arrives inverted', () => {
    // Current tail is at ordinal 9 (next expected is 10)
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(9)],
    })
    expect(state.nextExpectedOrdinal).toBe(10)

    // Message B (ordinal 11) arrives BEFORE message A (ordinal 10)
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(202, 11),
    })

    // Assert: ordinal 11 is NOT rendered yet! Messages list still only has [9]
    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([9])
    expect(state.nextExpectedOrdinal).toBe(10)
    expect(state.buffer[11]).toBeDefined()

    // Now Message A (ordinal 10) arrives
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(201, 10),
    })

    // Assert: Message 10 is rendered, and buffer automatically drained message 11 in order!
    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([9, 10, 11])
    expect(state.nextExpectedOrdinal).toBe(12)
    expect(state.buffer).toEqual({})
  })

  it('Z9i: repairs gaps via cursor fetch and preserves strict feed_ordinal ordering', () => {
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(5)],
    })
    expect(state.nextExpectedOrdinal).toBe(6)

    // Receive event with ordinal 8 (gap: 6 and 7 missing)
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(303, 8),
    })

    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([5])
    expect(state.buffer[8]).toBeDefined()

    // Gap repair fetches missing range [6, 7]
    state = feedReducer(state, {
      type: 'GAP_REPAIRED',
      payload: [createMessage(6), createMessage(7)],
    })

    // Assert: 6, 7 are added, and 8 is drained from buffer
    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([5, 6, 7, 8])
    expect(state.nextExpectedOrdinal).toBe(9)
    expect(state.buffer).toEqual({})
  })

  it('F-169: handles real API repair responses containing missing rows, buffered row, and subsequent rows without stale buffer keys', () => {
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(5)],
    })
    expect(state.nextExpectedOrdinal).toBe(6)

    // Receive event with ordinal 8 (gap: 6 and 7 missing)
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(303, 8),
    })

    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([5])
    expect(state.buffer[8]).toBeDefined()

    // Real API cursor query `fetchFeed(after=5, limit=500)` returns [6, 7, 8, 9]
    // Notice that 8 is both in the buffer and in the API response, plus 9 is returned.
    state = feedReducer(state, {
      type: 'GAP_REPAIRED',
      payload: [createMessage(6), createMessage(7), createMessage(8), createMessage(9)],
    })

    // Assert:
    // 1. All rows 5..9 are merged in exact contiguous order without duplicates
    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([5, 6, 7, 8, 9])
    // 2. nextExpectedOrdinal advances to 10
    expect(state.nextExpectedOrdinal).toBe(10)
    // 3. Stale buffer[8] is completely removed, buffer is empty
    expect(state.buffer).toEqual({})
    expect(Object.keys(state.buffer)).toHaveLength(0)
  })

  it('F-169: merges only contiguous prefix from nextExpectedOrdinal and retains non-contiguous rows in buffer', () => {
    let state = feedReducer(initialFeedState, {
      type: 'INIT_MESSAGES',
      payload: [createMessage(5)],
    })
    expect(state.nextExpectedOrdinal).toBe(6)

    // Receive event with ordinal 8
    state = feedReducer(state, {
      type: 'RECEIVE_EVENT',
      payload: createEvent(303, 8),
    })
    expect(state.buffer[8]).toBeDefined()

    // Real API cursor query returns [6, 7, 8, 11] (notice 9 and 10 are still missing!)
    state = feedReducer(state, {
      type: 'GAP_REPAIRED',
      payload: [createMessage(6), createMessage(7), createMessage(8), createMessage(11)],
    })

    // Assert:
    // 1. Only contiguous prefix 6, 7, 8 is merged into messages
    expect(state.messages.map((m) => m.feed_ordinal)).toEqual([5, 6, 7, 8])
    // 2. nextExpectedOrdinal is 9 (waiting for 9)
    expect(state.nextExpectedOrdinal).toBe(9)
    // 3. buffer[8] was purged, but non-contiguous row 11 is retained in buffer
    expect(state.buffer[8]).toBeUndefined()
    expect(state.buffer[11]).toBeDefined()
    expect(state.buffer[11].feed_ordinal).toBe(11)
  })
})

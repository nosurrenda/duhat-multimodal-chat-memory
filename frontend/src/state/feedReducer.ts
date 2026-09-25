import type { Event, Message } from '../types'

export interface FeedState {
  messages: Message[]
  nextExpectedOrdinal: number
  buffer: Record<number, Event>
  isRepairing: boolean
  lastEventId: number
}

export type FeedAction =
  | { type: 'INIT_MESSAGES'; payload: Message[] }
  | { type: 'PREPEND_HISTORY'; payload: Message[] }
  | { type: 'RECEIVE_EVENT'; payload: Event }
  | { type: 'GAP_REPAIRED'; payload: Message[] }
  | { type: 'SET_REPAIRING'; payload: boolean }
  | { type: 'SET_LAST_EVENT_ID'; payload: number }

export const initialFeedState: FeedState = {
  messages: [],
  nextExpectedOrdinal: 0,
  buffer: {},
  isRepairing: false,
  lastEventId: 0,
}

export function feedReducer(state: FeedState, action: FeedAction): FeedState {
  switch (action.type) {
    case 'INIT_MESSAGES': {
      const messages = [...action.payload].sort((a, b) => a.feed_ordinal - b.feed_ordinal)
      const maxOrdinal = messages.length > 0 ? messages[messages.length - 1].feed_ordinal : -1
      let nextExpected = maxOrdinal + 1

      // Clean from buffer any events that were already in the initial messages
      const newBuffer = { ...state.buffer }
      for (const m of messages) {
        delete newBuffer[m.feed_ordinal]
      }

      // Drain buffer if any events in buffer are contiguous from nextExpected
      const newMessages = [...messages]
      while (newBuffer[nextExpected]) {
        const bufferedEvent = newBuffer[nextExpected]
        delete newBuffer[nextExpected]
        newMessages.push(bufferedEvent.message)
        nextExpected++
      }

      // Defensive: remove any remaining buffer entries with ordinal < nextExpected
      for (const ordKey of Object.keys(newBuffer)) {
        if (Number(ordKey) < nextExpected) {
          delete newBuffer[Number(ordKey)]
        }
      }

      return {
        ...state,
        messages: newMessages,
        nextExpectedOrdinal: nextExpected,
        buffer: newBuffer,
      }
    }

    case 'RECEIVE_EVENT': {
      const event = action.payload
      const { feed_ordinal } = event
      const updatedLastEventId = Math.max(state.lastEventId, event.id)

      // Case 1: Already seen / duplicate or lower than expected
      if (feed_ordinal < state.nextExpectedOrdinal) {
        // Defensive check: if message isn't in list, insert in sorted position
        const exists = state.messages.some((m) => m.id === event.message.id || m.feed_ordinal === feed_ordinal)
        if (exists) {
          return { ...state, lastEventId: updatedLastEventId }
        }
        const updated = [...state.messages, event.message].sort((a, b) => a.feed_ordinal - b.feed_ordinal)
        return {
          ...state,
          messages: updated,
          lastEventId: updatedLastEventId,
        }
      }

      // Case 2: Exact next expected ordinal
      if (feed_ordinal === state.nextExpectedOrdinal) {
        const newMessages = [...state.messages, event.message]
        let nextExpected = state.nextExpectedOrdinal + 1
        const newBuffer = { ...state.buffer }

        // Drain any contiguous events from buffer
        while (newBuffer[nextExpected]) {
          const nextEvent = newBuffer[nextExpected]
          delete newBuffer[nextExpected]
          newMessages.push(nextEvent.message)
          nextExpected++
        }

        // Clean stale lower keys
        for (const ordKey of Object.keys(newBuffer)) {
          if (Number(ordKey) < nextExpected) {
            delete newBuffer[Number(ordKey)]
          }
        }

        return {
          ...state,
          messages: newMessages,
          nextExpectedOrdinal: nextExpected,
          buffer: newBuffer,
          lastEventId: updatedLastEventId,
        }
      }

      // Case 3: feed_ordinal > nextExpectedOrdinal -> GAP DETECTED!
      // Do NOT render yet; hold in buffer until missing ordinals are repaired.
      return {
        ...state,
        buffer: {
          ...state.buffer,
          [feed_ordinal]: event,
        },
        lastEventId: updatedLastEventId,
      }
    }

    case 'PREPEND_HISTORY': {
      const byID = new Map(state.messages.map((message) => [message.id, message]))
      for (const message of action.payload) {
        byID.set(message.id, message)
      }
      return {
        ...state,
        messages: [...byID.values()].sort((left, right) => left.feed_ordinal - right.feed_ordinal),
      }
    }

    case 'GAP_REPAIRED': {
      const fetched = action.payload
      if (fetched.length === 0) {
        return { ...state, isRepairing: false }
      }

      // F-169 Fix:
      // 1. Remove buffer entries whose message was fetched from the cursor endpoint
      const newBuffer = { ...state.buffer }
      for (const m of fetched) {
        delete newBuffer[m.feed_ordinal]
      }

      // 2. Build candidate map from fetched rows and remaining buffer events
      const candidateMap = new Map<number, Message>()
      for (const m of fetched) {
        candidateMap.set(m.feed_ordinal, m)
      }
      for (const [ordStr, evt] of Object.entries(newBuffer)) {
        const ord = Number(ordStr)
        if (!candidateMap.has(ord)) {
          candidateMap.set(ord, evt.message)
        }
      }

      // 3. Merge ONLY the contiguous prefix beginning at nextExpectedOrdinal
      const existingIds = new Set(state.messages.map((m) => m.id))
      const combined = [...state.messages]
      let expected = state.nextExpectedOrdinal

      while (candidateMap.has(expected)) {
        const msg = candidateMap.get(expected)!
        if (!existingIds.has(msg.id)) {
          combined.push(msg)
          existingIds.add(msg.id)
        }
        candidateMap.delete(expected)
        delete newBuffer[expected]
        expected++
      }

      // 4. Retain any remaining non-contiguous fetched rows or events in the buffer
      for (const [ord, msg] of candidateMap.entries()) {
        if (ord > expected && !newBuffer[ord]) {
          newBuffer[ord] = {
            id: 0,
            feed_ordinal: ord,
            message: msg,
          }
        }
      }

      // 5. Purge any stale entries with ordinal < expected
      for (const ordKey of Object.keys(newBuffer)) {
        if (Number(ordKey) < expected) {
          delete newBuffer[Number(ordKey)]
        }
      }

      return {
        ...state,
        messages: combined,
        nextExpectedOrdinal: expected,
        buffer: newBuffer,
        isRepairing: false,
      }
    }

    case 'SET_REPAIRING':
      return { ...state, isRepairing: action.payload }

    case 'SET_LAST_EVENT_ID':
      return { ...state, lastEventId: action.payload }

    default:
      return state
  }
}

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { fetchFeed, fetchFeedBefore } from '../api'
import type { ConnectionStatus, Event } from '../types'
import { feedReducer, initialFeedState } from './feedReducer'

export function useFeed() {
  const pageSize = 100
  const [state, dispatch] = useReducer(feedReducer, initialFeedState)
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [isLoadingInitial, setIsLoadingInitial] = useState(true)
  const [isLoadingOlder, setIsLoadingOlder] = useState(false)
  const [hasOlder, setHasOlder] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const isRepairingRef = useRef(false)

  // Keep ref up to date with state
  const stateRef = useRef(state)
  stateRef.current = state

  // Initial load is bounded; history is prepended only when the user scrolls up.
  useEffect(() => {
    let cancelled = false
    async function loadInitial() {
      try {
        setError(null)
        setIsLoadingInitial(true)
        const messages = await fetchFeedBefore(Number.MAX_SAFE_INTEGER, pageSize)
        if (!cancelled) {
          dispatch({ type: 'INIT_MESSAGES', payload: messages })
          setHasOlder(messages.length === pageSize)
          setIsLoadingInitial(false)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load initial feed')
          setIsLoadingInitial(false)
        }
      }
    }
    loadInitial()
    return () => {
      cancelled = true
    }
  }, [])

  const loadOlder = useCallback(async () => {
    if (isLoadingOlder || !hasOlder || stateRef.current.messages.length === 0) return
    const oldest = stateRef.current.messages[0]
    setIsLoadingOlder(true)
    try {
      const messages = await fetchFeedBefore(oldest.feed_ordinal, pageSize)
      dispatch({ type: 'PREPEND_HISTORY', payload: messages })
      setHasOlder(messages.length === pageSize)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load older history')
    } finally {
      setIsLoadingOlder(false)
    }
  }, [hasOlder, isLoadingOlder])

  // Gap repair effect: triggers when buffer has items and not currently repairing
  useEffect(() => {
    const hasBuffered = Object.keys(state.buffer).length > 0
    if (hasBuffered && !state.isRepairing && !isRepairingRef.current) {
      isRepairingRef.current = true
      dispatch({ type: 'SET_REPAIRING', payload: true })

      const startAfter = state.nextExpectedOrdinal - 1
      fetchFeed(startAfter, 500)
        .then((repairedMessages) => {
          dispatch({ type: 'GAP_REPAIRED', payload: repairedMessages })
        })
        .catch((err) => {
          console.error('Gap repair fetch failed:', err)
          dispatch({ type: 'SET_REPAIRING', payload: false })
        })
        .finally(() => {
          isRepairingRef.current = false
        })
    }
  }, [state.buffer, state.isRepairing, state.nextExpectedOrdinal])

  // Connect SSE
  useEffect(() => {
    let isMounted = true
    const sseUrl = '/api/demo/events'

    function connect() {
      setStatus('connecting')
      const es = new EventSource(sseUrl)
      eventSourceRef.current = es

      es.onopen = () => {
        if (isMounted) {
          setStatus('connected')
          setError(null)
        }
      }

      es.addEventListener('message', (e: MessageEvent) => {
        if (!isMounted) return
        try {
          const parsed: Event = JSON.parse(e.data)
          dispatch({ type: 'RECEIVE_EVENT', payload: parsed })
        } catch (err) {
          console.error('Error parsing SSE event data:', err, e.data)
        }
      })

      es.onerror = () => {
        if (isMounted) {
          setStatus('reconnecting')
        }
      }
    }

    connect()

    return () => {
      isMounted = false
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
      setStatus('disconnected')
    }
  }, [])

  const manuallyReceiveEvent = useCallback((event: Event) => {
    dispatch({ type: 'RECEIVE_EVENT', payload: event })
  }, [])

  return {
    messages: state.messages,
    nextExpectedOrdinal: state.nextExpectedOrdinal,
    isRepairing: state.isRepairing,
    isLoadingInitial,
    isLoadingOlder,
    hasOlder,
    loadOlder,
    status,
    error,
    manuallyReceiveEvent,
  }
}

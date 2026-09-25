import type { DraftResponse, Event, FeedResponse, Message } from './types'

const BASE_URL = ''

export function getMediaUrl(mediaId: string): string {
  return `${BASE_URL}/api/demo/media?id=${encodeURIComponent(mediaId)}`
}

export async function fetchFeed(after: number = -1, limit: number = 100): Promise<Message[]> {
  const query = new URLSearchParams()
  if (after >= 0) {
    query.set('after', String(after))
  }
  query.set('limit', String(limit))

  const res = await fetch(`${BASE_URL}/api/demo/feed?${query.toString()}`)
  if (!res.ok) {
    throw new Error(`Feed fetch failed: ${res.status} ${res.statusText}`)
  }
  const data: FeedResponse = await res.json()
  return data.messages || []
}

// The newest page is loaded first; older history is requested on scroll.
export async function fetchFeedBefore(before: number, limit: number = 100): Promise<Message[]> {
  const query = new URLSearchParams({ before: String(before), limit: String(limit) })
  const res = await fetch(`${BASE_URL}/api/demo/feed?${query.toString()}`)
  if (!res.ok) {
    throw new Error(`Feed fetch failed: ${res.status} ${res.statusText}`)
  }
  const data: FeedResponse = await res.json()
  return data.messages || []
}

export async function uploadDraft(file: File): Promise<DraftResponse> {
  const res = await fetch(`${BASE_URL}/api/demo/drafts`, {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
    },
    body: file,
  })
  if (!res.ok) {
    throw new Error(`Draft upload failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function sendMessage(text: string, mediaIds: string[] = []): Promise<Event> {
  const res = await fetch(`${BASE_URL}/api/demo/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      text,
      media_ids: mediaIds,
    }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData.error || `Send message failed: ${res.status}`)
  }
  return res.json()
}

export async function searchStub(query: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/search?q=${encodeURIComponent(query)}`)
  if (res.status === 501) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || 'Search is not available in Phase 2.7 (unimplemented stub per Z6b)')
  }
  throw new Error(`Unexpected search response: ${res.status}`)
}

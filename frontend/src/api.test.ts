import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchFeedBefore, getMediaUrl, searchStub } from './api'
import type { Message } from './types'

function makeMsg(feedOrdinal: number): Message {
  return {
    id: `m:${feedOrdinal}`,
    sender_id: 's',
    sender_name: 'Sender',
    text: `Msg ${feedOrdinal}`,
    occurred_at: '2026-09-23T12:00:00Z',
    feed_ordinal: feedOrdinal,
    attachments: [],
  }
}

describe('API functions (F-168, F-171, F-172)', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('F-172: getMediaUrl constructs clean scoped demo media URL without exposing raw MinIO', () => {
    const url = getMediaUrl('media_123:foo.png')
    expect(url).toBe('/api/demo/media?id=media_123%3Afoo.png')
  })

  it('F-171: searchStub throws descriptive error when backend returns 501', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      status: 501,
      json: async () => ({ error: 'search is not available yet' }),
    } as unknown as Response)

    await expect(searchStub('query')).rejects.toThrow('search is not available yet')
  })

  it('fetches one oldest-to-newest page with an exclusive before cursor', async () => {
    const page: Message[] = [makeMsg(500), makeMsg(501)]
    globalThis.fetch = vi.fn().mockImplementation((urlStr: string) => {
      const url = new URL(urlStr, 'http://localhost')
      expect(url.searchParams.get('before')).toBe('502')
      expect(url.searchParams.get('limit')).toBe('100')
      return Promise.resolve({
        ok: true,
        json: async () => ({ messages: page }),
      } as Response)
    })

    await expect(fetchFeedBefore(502)).resolves.toEqual(page)
  })
})

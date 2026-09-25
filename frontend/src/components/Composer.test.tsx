import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { Composer } from './Composer'

describe('Composer (F-170 Draft Error Handling & URL Cleanup)', () => {
  let createdUrls: string[] = []
  let revokedUrls: string[] = []

  beforeEach(() => {
    createdUrls = []
    revokedUrls = []
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn((file: File) => {
        const url = `blob:http://localhost/${file.name}`
        createdUrls.push(url)
        return url
      }),
      revokeObjectURL: vi.fn((url: string) => {
        revokedUrls.push(url)
      }),
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('blocks sending when a draft upload fails (F-170)', async () => {
    vi.spyOn(api, 'uploadDraft').mockRejectedValue(new Error('MinIO connection refused'))
    const sendSpy = vi.spyOn(api, 'sendMessage')

    const { container } = render(<Composer />)

    const file = new File(['fake content'], 'test.png', { type: 'image/png' })
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement

    fireEvent.change(fileInput, { target: { files: [file] } })

    // Wait for the upload failure to be reflected in DOM
    await waitFor(() => {
      expect(screen.getByText('Failed')).toBeInTheDocument()
    })

    const sendBtn = screen.getByTitle(/Send message/i)
    expect(sendBtn).toBeDisabled()

    // Clicking send should NOT invoke sendMessage
    fireEvent.click(sendBtn)
    expect(sendSpy).not.toHaveBeenCalled()
  })

  it('revokes draft object URLs on unmount (F-170)', async () => {
    vi.spyOn(api, 'uploadDraft').mockResolvedValue({ id: 'draft-1', content_type: 'image/png' })

    const { container, unmount } = render(<Composer />)

    const file = new File(['fake content'], 'test.png', { type: 'image/png' })
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement

    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(createdUrls).toHaveLength(1)
    })

    unmount()

    expect(revokedUrls).toContain(createdUrls[0])
  })

  it('revokes draft object URLs on successful send (F-170)', async () => {
    vi.spyOn(api, 'uploadDraft').mockResolvedValue({ id: 'draft-abc', content_type: 'image/png' })
    const sendSpy = vi.spyOn(api, 'sendMessage').mockResolvedValue({
      id: 1,
      feed_ordinal: 7080,
      message: {
        id: 'msg:7080',
        sender_id: 'demo_viewer',
        sender_name: 'Demo Viewer',
        text: 'Hello image',
        occurred_at: '2026-09-23T12:00:00Z',
        feed_ordinal: 7080,
        attachments: [{ id: 'draft-abc', content_type: 'image/png' }],
      },
    })

    const { container } = render(<Composer />)

    const file = new File(['fake content'], 'photo.png', { type: 'image/png' })
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement

    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText(/Type a message into Demo channel/i)
    fireEvent.change(textarea, { target: { value: 'Hello image' } })

    const sendBtn = screen.getByTitle(/Send message/i)
    expect(sendBtn).not.toBeDisabled()
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(sendSpy).toHaveBeenCalledWith('Hello image', ['draft-abc'])
      expect(revokedUrls).toContain(createdUrls[0])
    })
  })
})

import React, { useEffect, useRef, useState } from 'react'
import { sendMessage, uploadDraft } from '../api'

interface DraftItem {
  id?: string
  name: string
  previewUrl: string
  uploading: boolean
  error?: string
}

interface ComposerProps {
  onSent?: () => void
}

export const Composer: React.FC<ComposerProps> = ({ onSent }) => {
  const [text, setText] = useState('')
  const [drafts, setDrafts] = useState<DraftItem[]>([])
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const draftsRef = useRef<DraftItem[]>([])
  draftsRef.current = drafts

  // F-170: Revoke object URLs on component unmount
  useEffect(() => {
    return () => {
      draftsRef.current.forEach((d) => URL.revokeObjectURL(d.previewUrl))
    }
  }, [])

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    const newItems: DraftItem[] = Array.from(files).map((file) => ({
      name: file.name,
      previewUrl: URL.createObjectURL(file),
      uploading: true,
    }))

    setDrafts((prev) => [...prev, ...newItems])

    // Upload each draft
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      try {
        const res = await uploadDraft(file)
        setDrafts((prev) =>
          prev.map((item) =>
            item.name === file.name && item.uploading
              ? { ...item, id: res.id, uploading: false }
              : item
          )
        )
      } catch (err) {
        setDrafts((prev) =>
          prev.map((item) =>
            item.name === file.name && item.uploading
              ? { ...item, uploading: false, error: err instanceof Error ? err.message : 'Upload failed' }
              : item
          )
        )
      }
    }

    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleRemoveDraft = (index: number) => {
    setDrafts((prev) => {
      const next = [...prev]
      URL.revokeObjectURL(next[index].previewUrl)
      next.splice(index, 1)
      return next
    })
  }

  const hasUploading = drafts.some((d) => d.uploading)
  const hasDraftError = drafts.some((d) => Boolean(d.error))
  const allDraftsHaveId = drafts.every((d) => Boolean(d.id))
  const hasContent = text.trim().length > 0 || drafts.length > 0

  // F-170: Send is disabled if any draft is uploading, has error, or lacks an assigned ID
  const canSend = hasContent && !isSending && !hasUploading && !hasDraftError && allDraftsHaveId

  const handleSend = async () => {
    if (!canSend) return

    const trimmed = text.trim()
    const mediaIds = drafts.map((d) => d.id).filter((id): id is string => Boolean(id))

    // F-170: Enforce that all drafts have IDs
    if (drafts.length > 0 && mediaIds.length !== drafts.length) {
      setSendError('Some attachments failed to upload. Please remove them before sending.')
      return
    }

    try {
      setIsSending(true)
      setSendError(null)
      await sendMessage(trimmed || '[Image Attachment]', mediaIds)
      
      // F-170: Revoke preview URLs on successful send
      drafts.forEach((d) => URL.revokeObjectURL(d.previewUrl))
      setText('')
      setDrafts([])
      onSent?.()
      textareaRef.current?.focus()
    } catch (err) {
      setSendError(err instanceof Error ? err.message : 'Failed to send')
    } finally {
      setIsSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="composer-container border-t border-zinc-800 bg-zinc-950/90 backdrop-blur px-4 py-3 sticky bottom-0 z-20">
      <div className="max-w-5xl mx-auto">
        {sendError && (
          <div className="mb-2 text-xs font-mono text-rose-400 bg-rose-950/40 border border-rose-900/60 px-3 py-1.5 rounded flex justify-between items-center">
            <span>Error: {sendError}</span>
            <button onClick={() => setSendError(null)} className="text-zinc-500 hover:text-zinc-300">×</button>
          </div>
        )}

        {/* Draft Previews */}
        {drafts.length > 0 && (
          <div className="flex gap-2 mb-2 overflow-x-auto pb-1">
            {drafts.map((draft, idx) => (
              <div
                key={idx}
                className="relative group shrink-0 w-20 h-20 rounded-md border border-zinc-800 bg-zinc-900 overflow-hidden"
              >
                <img src={draft.previewUrl} alt={draft.name} className="w-full h-full object-cover" />
                {draft.uploading && (
                  <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                    <svg className="w-5 h-5 text-zinc-300 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  </div>
                )}
                {draft.error && (
                  <div className="absolute inset-0 bg-rose-950/80 flex items-center justify-center text-[10px] text-rose-300 font-mono text-center p-1">
                    Failed
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => handleRemoveDraft(idx)}
                  className="absolute top-1 right-1 w-4 h-4 bg-zinc-900/80 hover:bg-zinc-800 rounded-full text-zinc-300 flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Remove attachment"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Input Bar */}
        <div className="flex items-end gap-2 bg-zinc-900/80 border border-zinc-800 rounded-xl p-2 focus-within:border-zinc-700 transition-colors">
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileSelect}
            accept="image/*"
            multiple
            className="hidden"
          />

          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="p-2 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors shrink-0"
            title="Attach image (Step 4 Draft Upload)"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
            </svg>
          </button>

          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message into Demo channel..."
            rows={1}
            className="flex-1 bg-transparent border-0 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none resize-none py-1.5 px-2 max-h-32 min-h-[36px]"
          />

          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className={`p-2 rounded-lg transition-all shrink-0 ${
              canSend
                ? 'bg-zinc-100 hover:bg-white text-zinc-900 shadow-sm cursor-pointer'
                : 'bg-zinc-800/60 text-zinc-600 cursor-not-allowed'
            }`}
            title="Send message (Step 5 Atomic Transaction)"
          >
            {isSending ? (
              <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            )}
          </button>
        </div>

        <div className="flex justify-between items-center mt-1.5 px-1 text-[11px] font-mono text-zinc-500">
          <span>Target: <span className="text-zinc-400">demo_channel</span></span>
          <span className="hidden sm:inline">Press ↵ to send · Shift+↵ for new line</span>
        </div>
      </div>
    </div>
  )
}

import React, { useEffect } from 'react'
import { getMediaUrl } from '../api'

interface ImageModalProps {
  mediaId: string | null
  onClose: () => void
}

export const ImageModal: React.FC<ImageModalProps> = ({ mediaId, onClose }) => {
  const [hasError, setHasError] = React.useState(false)

  useEffect(() => {
    setHasError(false)
  }, [mediaId])

  useEffect(() => {
    if (!mediaId) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [mediaId, onClose])

  if (!mediaId) return null

  const mediaUrl = getMediaUrl(mediaId)

  return (
    <div
      className="fixed inset-0 z-50 bg-black/85 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Image Preview"
    >
      <div
        className="relative max-w-4xl max-h-[90vh] bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/60">
          <div className="flex items-center gap-2 overflow-hidden">
            <span className="text-xs font-mono text-zinc-400 truncate max-w-xs sm:max-w-md">
              {mediaId}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <a
              href={mediaUrl}
              target="_blank"
              rel="noreferrer"
              className="text-xs font-mono text-zinc-400 hover:text-zinc-200 underline"
            >
              Open raw
            </a>
            <button
              onClick={onClose}
              className="w-7 h-7 rounded-md bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300 flex items-center justify-center transition-colors text-sm"
              aria-label="Close image preview"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="p-2 flex items-center justify-center bg-zinc-950/80 overflow-auto">
          {hasError ? (
            <div className="p-8 text-center text-xs font-mono text-zinc-500 flex flex-col items-center gap-2">
              <svg className="w-8 h-8 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
              </svg>
              <span>Media preview unavailable</span>
            </div>
          ) : (
            <img
              src={mediaUrl}
              alt={mediaId}
              className="max-h-[75vh] max-w-full object-contain rounded"
              onError={() => setHasError(true)}
            />
          )}
        </div>
      </div>
    </div>
  )
}

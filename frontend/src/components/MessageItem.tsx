import React from 'react'
import { getMediaUrl } from '../api'
import type { Message } from '../types'

interface MessageItemProps {
  message: Message
  onSelectImage?: (mediaId: string) => void
}

function formatTime(isoString: string): string {
  try {
    const date = new Date(isoString)
    if (isNaN(date.getTime())) return isoString
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return isoString
  }
}

function getAvatarColor(name: string): string {
  const colors = [
    'bg-blue-900/60 text-blue-300 border-blue-700/50',
    'bg-emerald-900/60 text-emerald-300 border-emerald-700/50',
    'bg-purple-900/60 text-purple-300 border-purple-700/50',
    'bg-amber-900/60 text-amber-300 border-amber-700/50',
    'bg-cyan-900/60 text-cyan-300 border-cyan-700/50',
    'bg-rose-900/60 text-rose-300 border-rose-700/50',
  ]
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i)
  }
  return colors[Math.abs(hash) % colors.length]
}

interface AttachmentThumbnailProps {
  attachment: { id: string; content_type: string }
  onSelectImage?: (mediaId: string) => void
}

const AttachmentThumbnail: React.FC<AttachmentThumbnailProps> = ({ attachment, onSelectImage }) => {
  const [hasError, setHasError] = React.useState(false)

  return (
    <div
      onClick={() => onSelectImage?.(attachment.id)}
      className="relative group/attachment overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/90 hover:border-zinc-700 cursor-pointer transition-all flex items-center p-1.5 gap-2.5 max-w-xs shadow-sm"
      title={`Click to preview ${attachment.id}`}
    >
      <div className="w-14 h-14 rounded bg-zinc-950 overflow-hidden shrink-0 flex items-center justify-center border border-zinc-800/80 relative">
        {hasError ? (
          <div className="text-zinc-600 flex items-center justify-center w-full h-full">
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          </div>
        ) : (
          <img
            src={getMediaUrl(attachment.id)}
            alt={attachment.id}
            className="w-full h-full object-cover"
            onError={() => setHasError(true)}
          />
        )}
      </div>
      <div className="min-w-0 pr-1">
        <div className="text-[11px] font-mono text-zinc-300 truncate max-w-[140px] font-medium">
          {attachment.id}
        </div>
        <div className="text-[10px] font-mono text-zinc-500 uppercase mt-0.5">
          {attachment.content_type || 'image'}
        </div>
      </div>
    </div>
  )
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, onSelectImage }) => {
  const initials = (message.sender_name || 'U')
    .split(' ')
    .map((part) => part[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

  const avatarStyle = getAvatarColor(message.sender_name || 'Unknown')
  const isDemo = message.sender_id === 'demo_viewer' || message.sender_name === 'Demo'

  return (
    <div
      className={`message-row group px-4 py-2 hover:bg-zinc-900/40 transition-colors flex gap-3 ${
        isDemo ? 'bg-zinc-900/20' : ''
      }`}
      data-feed-ordinal={message.feed_ordinal}
    >
      <div
        className={`w-7 h-7 rounded-md border flex items-center justify-center font-mono text-xs font-semibold shrink-0 mt-0.5 ${avatarStyle}`}
      >
        {initials}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-xs font-medium text-zinc-200">
            {message.sender_name || 'Anonymous'}
          </span>
          <span className="text-[11px] font-mono text-zinc-500">
            {formatTime(message.occurred_at)}
          </span>
          <span className="text-[10px] font-mono text-zinc-600 opacity-60 group-hover:opacity-100 transition-opacity">
            #{message.feed_ordinal}
          </span>
          {isDemo && (
            <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
              Live
            </span>
          )}
        </div>

        <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap break-words">
          {message.text}
        </p>

        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {message.attachments.map((att) => (
              <AttachmentThumbnail
                key={att.id}
                attachment={att}
                onSelectImage={onSelectImage}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

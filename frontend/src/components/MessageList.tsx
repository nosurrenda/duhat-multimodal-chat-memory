import React, { useEffect, useRef } from 'react'
import type { Message } from '../types'
import { MessageItem } from './MessageItem'

interface MessageListProps {
  messages: Message[]
  onSelectImage?: (mediaId: string) => void
  loadOlder?: () => void
  hasOlder?: boolean
  isLoadingOlder?: boolean
}

export const MessageList: React.FC<MessageListProps> = ({ messages, onSelectImage, loadOlder, hasOlder, isLoadingOlder }) => {
  const bottomRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const isAutoScrollRef = useRef(true)
  const prependHeightRef = useRef<number | null>(null)

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    isAutoScrollRef.current = isAtBottom
    if (el.scrollTop < 120 && hasOlder && !isLoadingOlder) {
      prependHeightRef.current = el.scrollHeight
      loadOlder?.()
    }
  }

  useEffect(() => {
    const list = listRef.current
    if (list && prependHeightRef.current !== null) {
      list.scrollTop += list.scrollHeight - prependHeightRef.current
      prependHeightRef.current = null
    }
    if (isAutoScrollRef.current && typeof bottomRef.current?.scrollIntoView === 'function') {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages.length])

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-zinc-500">
        <div className="w-12 h-12 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-center mb-3 text-zinc-600">
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </div>
        <p className="text-sm font-medium text-zinc-400">Connecting to Demo History...</p>
        <p className="text-xs text-zinc-600 max-w-sm mt-1">
          Loading the unified 25-channel corpus projection ordered by feed ordinal.
        </p>
      </div>
    )
  }

  return (
    <div
      ref={listRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto divide-y divide-zinc-900/60 custom-scrollbar"
    >
      <div className="py-2">
        {isLoadingOlder && <div className="h-8" aria-label="Loading older messages" />}
        {messages.map((msg) => (
          <MessageItem key={msg.id} message={msg} onSelectImage={onSelectImage} />
        ))}
      </div>
      <div ref={bottomRef} />
    </div>
  )
}

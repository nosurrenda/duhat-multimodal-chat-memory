export interface Attachment {
  id: string
  content_type: string
}

export interface Message {
  id: string
  sender_id: string
  sender_name: string
  text: string
  occurred_at: string
  feed_ordinal: number
  attachments?: Attachment[]
}

export interface Event {
  id: number
  feed_ordinal: number
  message: Message
}

export interface FeedResponse {
  messages: Message[]
}

export interface DraftResponse {
  id: string
  content_type: string
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'error'

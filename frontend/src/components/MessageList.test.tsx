import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Message } from '../types'
import { MessageList } from './MessageList'

describe('MessageList & D72 / Z9d Boundary', () => {
  const sampleMessages: Message[] = [
    {
      id: 'msg:0',
      sender_id: 'sender_xia_qingchuan',
      sender_name: 'Xia Qingchuan',
      text: 'Finished setting up bedroom corner.',
      occurred_at: '2024-11-20T09:00:00Z',
      feed_ordinal: 0,
    },
    {
      id: 'msg:1',
      sender_id: 'sender_su_mingche',
      sender_name: 'Su Mingche',
      text: 'Looks cozy and matches your style.',
      occurred_at: '2024-11-20T09:01:30Z',
      feed_ordinal: 1,
    },
  ]

  it('renders messages with sender names, text and ordinal without leaking channel_id (Z9d)', () => {
    const { container } = render(<MessageList messages={sampleMessages} />)

    expect(screen.getByText('Xia Qingchuan')).toBeInTheDocument()
    expect(screen.getByText('Finished setting up bedroom corner.')).toBeInTheDocument()
    expect(screen.getByText('#0')).toBeInTheDocument()

    const renderedHtml = container.innerHTML

    // Z9d assertion: No raw source channel_id or dataset naming convention
    const channelPattern = /dyadic_d\d+|multiparty_d\d+/i
    expect(channelPattern.test(renderedHtml)).toBe(false)
  })

  it('positive control Z9e: fails if a payload leaks a raw source channel_id', () => {
    // Deliberately violating message with raw channel_id in text or attributes
    const leakedMessage: Message = {
      id: 'msg:99',
      sender_id: 'test_user',
      sender_name: 'Test',
      text: 'Sent from channel dyadic_d10',
      occurred_at: '2024-11-20T09:00:00Z',
      feed_ordinal: 99,
    }

    const { container } = render(<MessageList messages={[leakedMessage]} />)
    const renderedHtml = container.innerHTML

    const channelPattern = /dyadic_d\d+|multiparty_d\d+/i
    // The positive control asserts that this leak is indeed detected!
    expect(channelPattern.test(renderedHtml)).toBe(true)
  })
})

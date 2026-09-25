import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SearchBar } from './SearchBar'

describe('SearchBar (Z6b Disabled State per F-171)', () => {
  it('renders strictly disabled input with Phase 5 badge and no interactive probe button', () => {
    render(<SearchBar />)

    const input = screen.getByRole('textbox', { name: /search/i })
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute('readonly')
    expect(input).toHaveAttribute('placeholder', 'Search disabled in Phase 2.7 (retrieval stub per Z6b)')

    expect(screen.getByText('Disabled · Phase 5')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /probe/i })).not.toBeInTheDocument()
  })
})


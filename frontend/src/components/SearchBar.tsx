import React from 'react'

export const SearchBar: React.FC = () => {
  return (
    <div className="search-bar-wrapper max-w-5xl mx-auto px-4 py-2">
      <div className="relative flex items-center">
        <div className="absolute left-3 text-zinc-600 pointer-events-none flex items-center">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <input
          type="text"
          disabled
          readOnly
          value=""
          placeholder="Search disabled in Phase 2.7 (retrieval stub per Z6b)"
          aria-label="Search conversation history"
          className="w-full bg-zinc-900/30 border border-zinc-800/50 rounded-lg pl-9 pr-32 py-1.5 text-xs text-zinc-500 placeholder-zinc-600 cursor-not-allowed select-none focus:outline-none"
        />
        <div className="absolute right-2.5 flex items-center">
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-900 text-zinc-500 border border-zinc-800 pointer-events-none">
            Disabled · Phase 5
          </span>
        </div>
      </div>
    </div>
  )
}

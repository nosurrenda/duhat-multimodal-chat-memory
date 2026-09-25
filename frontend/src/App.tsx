import React, { useState } from 'react'
import { Composer } from './components/Composer'
import { Header } from './components/Header'
import { ImageModal } from './components/ImageModal'
import { MessageList } from './components/MessageList'
import { SearchBar } from './components/SearchBar'
import { useFeed } from './state/useFeed'

export const App: React.FC = () => {
  const { messages, status, isRepairing, error, hasOlder, isLoadingOlder, loadOlder } = useFeed()
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)

  return (
    <div className="flex flex-col h-screen bg-zinc-950 text-zinc-100 antialiased font-sans selection:bg-zinc-800 selection:text-zinc-100">
      <Header status={status} messageCount={messages.length} isRepairing={isRepairing} />

      <SearchBar />

      {error && (
        <div className="mx-4 my-2 px-3 py-2 bg-rose-950/40 border border-rose-900/60 rounded-md text-xs font-mono text-rose-300 max-w-5xl mx-auto flex items-center justify-between">
          <span>⚠️ {error}</span>
          <span className="text-rose-400 text-[10px]">Check server on :8080</span>
        </div>
      )}

      <main className="flex-1 overflow-hidden flex flex-col max-w-5xl w-full mx-auto">
        <MessageList messages={messages} onSelectImage={setSelectedMediaId} hasOlder={hasOlder} isLoadingOlder={isLoadingOlder} loadOlder={loadOlder} />
      </main>

      <Composer />

      <ImageModal mediaId={selectedMediaId} onClose={() => setSelectedMediaId(null)} />
    </div>
  )
}

export default App

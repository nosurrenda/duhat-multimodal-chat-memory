import React from 'react'
import type { ConnectionStatus } from '../types'

interface HeaderProps {
  status: ConnectionStatus
  messageCount: number
  isRepairing: boolean
}

export const Header: React.FC<HeaderProps> = ({ status, messageCount, isRepairing }) => {
  const getStatusDisplay = () => {
    switch (status) {
      case 'connected':
        return {
          color: 'bg-emerald-500',
          label: 'Live Stream',
        }
      case 'connecting':
      case 'reconnecting':
        return {
          color: 'bg-amber-500 animate-pulse',
          label: status === 'connecting' ? 'Connecting...' : 'Reconnecting...',
        }
      case 'error':
        return {
          color: 'bg-rose-500',
          label: 'Connection Error',
        }
      case 'disconnected':
      default:
        return {
          color: 'bg-zinc-500',
          label: 'Offline',
        }
    }
  }

  const statusInfo = getStatusDisplay()

  return (
    <header className="header-container border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-20 px-4 py-3">
      <div className="max-w-5xl mx-auto flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-zinc-900 border border-zinc-800 flex items-center justify-center font-mono font-bold text-zinc-100 text-sm shadow-sm">
            V
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-semibold text-zinc-100 tracking-tight">
                HyperMem Chat Shell
              </h1>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                Phase 2.7
              </span>
            </div>
            <p className="text-xs text-zinc-400">
              Demo History Projection · <span className="font-mono text-zinc-300">{messageCount}</span> messages
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {isRepairing && (
            <div className="flex items-center gap-1.5 text-xs text-amber-400 font-mono bg-amber-950/50 px-2 py-0.5 rounded border border-amber-800/60 animate-pulse">
              <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span>Repairing Gap...</span>
            </div>
          )}

          <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 px-2.5 py-1 rounded-full text-xs">
            <span className={`w-2 h-2 rounded-full ${statusInfo.color}`} />
            <span className="text-zinc-300 font-mono text-[11px]">{statusInfo.label}</span>
          </div>

          <div className="hidden sm:flex items-center gap-1 text-[11px] font-mono text-zinc-400 bg-zinc-900/60 px-2 py-1 rounded border border-zinc-800/80">
            <span className="text-zinc-500">viewer:</span>
            <span className="text-zinc-200">demo_viewer</span>
          </div>
        </div>
      </div>
    </header>
  )
}

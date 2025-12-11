'use client'

import { createContext, useContext, useCallback, useEffect, useState, ReactNode } from 'react'

// Wallet types
export interface SolanaWallet {
  name: string
  icon: string
  installed: boolean
  connect: () => Promise<string | null>
  disconnect: () => Promise<void>
  signTransaction: (transaction: any) => Promise<any>
  publicKey: string | null
}

interface WalletContextType {
  wallets: SolanaWallet[]
  selectedWallet: SolanaWallet | null
  publicKey: string | null
  connected: boolean
  connecting: boolean
  connect: (walletName: string) => Promise<boolean>
  disconnect: () => Promise<void>
  signAndSendTransaction: (transaction: any) => Promise<string | null>
}

const WalletContext = createContext<WalletContextType | null>(null)

export const useWallet = () => {
  const context = useContext(WalletContext)
  if (!context) {
    throw new Error('useWallet must be used within SolanaWalletProvider')
  }
  return context
}

// Detect installed wallets
const detectWallets = (): SolanaWallet[] => {
  const wallets: SolanaWallet[] = []

  // Check if we're in browser
  if (typeof window === 'undefined') return wallets

  // Phantom
  if ((window as any).phantom?.solana) {
    const phantom = (window as any).phantom.solana
    wallets.push({
      name: 'Phantom',
      icon: 'https://phantom.app/img/phantom-icon-purple.svg',
      installed: true,
      connect: async () => {
        try {
          const resp = await phantom.connect()
          return resp.publicKey.toString()
        } catch {
          return null
        }
      },
      disconnect: async () => {
        await phantom.disconnect()
      },
      signTransaction: async (tx) => {
        return await phantom.signTransaction(tx)
      },
      publicKey: phantom.publicKey?.toString() || null,
    })
  }

  // Solflare
  if ((window as any).solflare) {
    const solflare = (window as any).solflare
    wallets.push({
      name: 'Solflare',
      icon: 'https://solflare.com/favicon.ico',
      installed: true,
      connect: async () => {
        try {
          await solflare.connect()
          return solflare.publicKey?.toString() || null
        } catch {
          return null
        }
      },
      disconnect: async () => {
        await solflare.disconnect()
      },
      signTransaction: async (tx) => {
        return await solflare.signTransaction(tx)
      },
      publicKey: solflare.publicKey?.toString() || null,
    })
  }

  // Backpack
  if ((window as any).backpack) {
    const backpack = (window as any).backpack
    wallets.push({
      name: 'Backpack',
      icon: 'https://backpack.app/favicon.ico',
      installed: true,
      connect: async () => {
        try {
          await backpack.connect()
          return backpack.publicKey?.toString() || null
        } catch {
          return null
        }
      },
      disconnect: async () => {
        await backpack.disconnect()
      },
      signTransaction: async (tx) => {
        return await backpack.signTransaction(tx)
      },
      publicKey: backpack.publicKey?.toString() || null,
    })
  }

  // Add placeholders for common wallets if not installed
  const walletNames = wallets.map(w => w.name)

  if (!walletNames.includes('Phantom')) {
    wallets.push({
      name: 'Phantom',
      icon: 'https://phantom.app/img/phantom-icon-purple.svg',
      installed: false,
      connect: async () => {
        window.open('https://phantom.app/', '_blank')
        return null
      },
      disconnect: async () => {},
      signTransaction: async () => null,
      publicKey: null,
    })
  }

  if (!walletNames.includes('Solflare')) {
    wallets.push({
      name: 'Solflare',
      icon: 'https://solflare.com/favicon.ico',
      installed: false,
      connect: async () => {
        window.open('https://solflare.com/', '_blank')
        return null
      },
      disconnect: async () => {},
      signTransaction: async () => null,
      publicKey: null,
    })
  }

  return wallets
}

interface SolanaWalletProviderProps {
  children: ReactNode
  autoConnect?: boolean
}

export const SolanaWalletProvider = ({
  children,
  autoConnect = false,
}: SolanaWalletProviderProps) => {
  const [wallets, setWallets] = useState<SolanaWallet[]>([])
  const [selectedWallet, setSelectedWallet] = useState<SolanaWallet | null>(null)
  const [publicKey, setPublicKey] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)

  // Detect wallets on mount
  useEffect(() => {
    const detected = detectWallets()
    setWallets(detected)

    // Auto-connect to previously connected wallet
    if (autoConnect) {
      const lastWallet = localStorage.getItem('solana_wallet')
      if (lastWallet) {
        const wallet = detected.find(w => w.name === lastWallet && w.installed)
        if (wallet && wallet.publicKey) {
          setSelectedWallet(wallet)
          setPublicKey(wallet.publicKey)
        }
      }
    }
  }, [autoConnect])

  const connect = useCallback(async (walletName: string): Promise<boolean> => {
    const wallet = wallets.find(w => w.name === walletName)
    if (!wallet) return false

    if (!wallet.installed) {
      await wallet.connect() // Opens install page
      return false
    }

    setConnecting(true)
    try {
      const pubKey = await wallet.connect()
      if (pubKey) {
        setSelectedWallet(wallet)
        setPublicKey(pubKey)
        localStorage.setItem('solana_wallet', walletName)
        return true
      }
      return false
    } catch (error) {
      console.error('Failed to connect wallet:', error)
      return false
    } finally {
      setConnecting(false)
    }
  }, [wallets])

  const disconnect = useCallback(async () => {
    if (selectedWallet) {
      await selectedWallet.disconnect()
    }
    setSelectedWallet(null)
    setPublicKey(null)
    localStorage.removeItem('solana_wallet')
  }, [selectedWallet])

  const signAndSendTransaction = useCallback(async (transaction: any): Promise<string | null> => {
    if (!selectedWallet || !selectedWallet.installed) {
      throw new Error('No wallet connected')
    }

    try {
      const signedTx = await selectedWallet.signTransaction(transaction)

      // Send the signed transaction
      // This would typically go through your backend or directly to RPC
      // For now, return a placeholder - the actual sending happens server-side
      return signedTx ? 'signed' : null
    } catch (error) {
      console.error('Failed to sign transaction:', error)
      return null
    }
  }, [selectedWallet])

  const value: WalletContextType = {
    wallets,
    selectedWallet,
    publicKey,
    connected: !!publicKey,
    connecting,
    connect,
    disconnect,
    signAndSendTransaction,
  }

  return (
    <WalletContext.Provider value={value}>
      {children}
    </WalletContext.Provider>
  )
}

// Wallet button component
interface WalletButtonProps {
  wallet: SolanaWallet
  onSelect: (name: string) => void
  selected?: boolean
  disabled?: boolean
}

export const WalletButton = ({
  wallet,
  onSelect,
  selected,
  disabled,
}: WalletButtonProps) => {
  return (
    <button
      onClick={() => onSelect(wallet.name)}
      disabled={disabled}
      className={`
        flex items-center gap-3 w-full px-4 py-3 rounded-lg border transition-colors
        ${selected
          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
          : 'border-gray-200 dark:border-polar-700 hover:border-gray-300 dark:hover:border-polar-600'
        }
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
      `}
    >
      <img
        src={wallet.icon}
        alt={wallet.name}
        className="w-8 h-8 rounded-lg"
        onError={(e) => {
          (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect fill="%23AB9FF2" width="24" height="24" rx="4"/></svg>'
        }}
      />
      <div className="flex-1 text-left">
        <div className="font-medium">{wallet.name}</div>
        {!wallet.installed && (
          <div className="text-xs text-gray-500">Click to install</div>
        )}
      </div>
      {wallet.installed && (
        <span className="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 rounded">
          Detected
        </span>
      )}
    </button>
  )
}

// Wallet multi-button (shows connected state)
export const WalletMultiButton = () => {
  const { publicKey, connected, connecting, disconnect, wallets, connect } = useWallet()
  const [showDropdown, setShowDropdown] = useState(false)

  if (connected && publicKey) {
    return (
      <div className="relative">
        <button
          onClick={() => setShowDropdown(!showDropdown)}
          className="flex items-center gap-2 px-4 py-2 bg-gray-100 dark:bg-polar-800 rounded-lg hover:bg-gray-200 dark:hover:bg-polar-700 transition-colors"
        >
          <span className="w-2 h-2 bg-green-500 rounded-full" />
          <span className="font-mono text-sm">
            {publicKey.slice(0, 4)}...{publicKey.slice(-4)}
          </span>
        </button>

        {showDropdown && (
          <div className="absolute top-full right-0 mt-2 w-48 bg-white dark:bg-polar-900 rounded-lg shadow-lg border border-gray-200 dark:border-polar-700 py-2">
            <button
              onClick={() => {
                navigator.clipboard.writeText(publicKey)
                setShowDropdown(false)
              }}
              className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-polar-800"
            >
              Copy address
            </button>
            <button
              onClick={() => {
                disconnect()
                setShowDropdown(false)
              }}
              className="w-full px-4 py-2 text-left text-sm text-red-500 hover:bg-gray-100 dark:hover:bg-polar-800"
            >
              Disconnect
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        onClick={() => setShowDropdown(!showDropdown)}
        disabled={connecting}
        className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors disabled:opacity-50"
      >
        {connecting ? 'Connecting...' : 'Connect Wallet'}
      </button>

      {showDropdown && (
        <div className="absolute top-full right-0 mt-2 w-64 bg-white dark:bg-polar-900 rounded-lg shadow-lg border border-gray-200 dark:border-polar-700 p-2">
          {wallets.map((wallet) => (
            <WalletButton
              key={wallet.name}
              wallet={wallet}
              onSelect={async (name) => {
                const success = await connect(name)
                if (success) setShowDropdown(false)
              }}
              disabled={connecting}
            />
          ))}
        </div>
      )}
    </div>
  )
}

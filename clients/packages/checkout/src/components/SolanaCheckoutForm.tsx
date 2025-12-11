'use client'

import type { CheckoutPublic } from '@polar-sh/sdk/models/components/checkoutpublic'
import type { CheckoutPublicConfirmed } from '@polar-sh/sdk/models/components/checkoutpublicconfirmed'
import type { CheckoutUpdatePublic } from '@polar-sh/sdk/models/components/checkoutupdatepublic'
import Button from '@polar-sh/ui/components/atoms/Button'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { UseFormReturn } from 'react-hook-form'
import { formatCurrencyNumber } from '../utils/money'
import {
  SolanaWalletProvider,
  useWallet,
  WalletButton,
} from './SolanaWalletProvider'

// Payment status indicator
const PaymentStatus = ({
  status,
  message,
}: {
  status: 'pending' | 'confirming' | 'confirmed' | 'failed'
  message?: string
}) => {
  const statusConfig = {
    pending: {
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-50 dark:bg-yellow-900/20',
      icon: '⏳',
      label: 'Waiting for payment...',
    },
    confirming: {
      color: 'text-blue-500',
      bgColor: 'bg-blue-50 dark:bg-blue-900/20',
      icon: '🔄',
      label: 'Confirming on-chain...',
    },
    confirmed: {
      color: 'text-green-500',
      bgColor: 'bg-green-50 dark:bg-green-900/20',
      icon: '✓',
      label: 'Payment confirmed!',
    },
    failed: {
      color: 'text-red-500',
      bgColor: 'bg-red-50 dark:bg-red-900/20',
      icon: '✗',
      label: message || 'Payment failed',
    },
  }

  const config = statusConfig[status]

  return (
    <div
      className={`flex items-center gap-2 px-4 py-2 rounded-lg ${config.bgColor}`}
    >
      <span className={`text-xl ${config.color}`}>{config.icon}</span>
      <span className={config.color}>{config.label}</span>
    </div>
  )
}

// QR Code display for mobile fallback
const QRCodeDisplay = ({ url, size = 200 }: { url: string; size?: number }) => {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)

  useEffect(() => {
    const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(url)}`
    setQrDataUrl(qrUrl)
  }, [url, size])

  if (!qrDataUrl) {
    return (
      <div
        className="flex items-center justify-center bg-gray-100 dark:bg-polar-800 rounded-lg animate-pulse"
        style={{ width: size, height: size }}
      >
        <span className="text-gray-400">Loading...</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-4">
      <img
        src={qrDataUrl}
        alt="Solana Pay QR Code"
        className="rounded-lg border border-gray-200 dark:border-polar-700"
        style={{ width: size, height: size }}
      />
      <p className="text-sm text-gray-500 dark:text-polar-400 text-center">
        Scan with your mobile wallet
      </p>
    </div>
  )
}

interface SolanaCheckoutFormProps {
  form: UseFormReturn<CheckoutUpdatePublic>
  checkout: CheckoutPublic
  update: (data: CheckoutUpdatePublic) => Promise<CheckoutPublic>
  confirm: (data: any) => Promise<CheckoutPublicConfirmed>
  loading: boolean
  loadingLabel: string | undefined
  disabled?: boolean
  isUpdatePending?: boolean
  apiBaseUrl?: string
}

// Inner component that uses wallet context
const SolanaCheckoutFormInner = ({
  checkout,
  confirm,
  loading,
  disabled,
  apiBaseUrl = '',
}: SolanaCheckoutFormProps) => {
  const {
    wallets,
    publicKey,
    connected,
    connecting,
    connect,
    disconnect,
  } = useWallet()

  const [paymentStatus, setPaymentStatus] = useState<
    'idle' | 'pending' | 'confirming' | 'confirmed' | 'failed'
  >('idle')
  const [solanaPayUrl, setSolanaPayUrl] = useState<string | null>(null)
  const [reference, setReference] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showQR, setShowQR] = useState(false)
  const [transactionSignature, setTransactionSignature] = useState<string | null>(null)

  const totalAmount = checkout.totalAmount || 0
  const formattedAmount = useMemo(
    () => formatCurrencyNumber(totalAmount, checkout.currency || 'usd', 2),
    [totalAmount, checkout.currency],
  )

  // Create payment request and get transaction to sign
  const initiatePayment = useCallback(async () => {
    setPaymentStatus('pending')
    setError(null)

    try {
      const response = await fetch(
        `${apiBaseUrl}/v1/integrations/solana/create-payment`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            checkout_id: checkout.id,
            payer_wallet: publicKey, // Include connected wallet
          }),
        },
      )

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Failed to create payment request')
      }

      const data = await response.json()
      setSolanaPayUrl(data.solana_pay_url)
      setReference(data.reference)

      return data
    } catch (err) {
      setPaymentStatus('failed')
      setError(err instanceof Error ? err.message : 'Failed to create payment')
      return null
    }
  }, [checkout.id, apiBaseUrl, publicKey])

  // Send transaction through connected wallet
  const payWithWallet = useCallback(async () => {
    if (!connected || !publicKey) {
      setError('Please connect your wallet first')
      return
    }

    setPaymentStatus('pending')
    setError(null)

    try {
      // Get the payment request from backend
      const paymentData = await initiatePayment()
      if (!paymentData) return

      // Get the transaction to sign from backend
      const txResponse = await fetch(
        `${apiBaseUrl}/v1/integrations/solana/create-transaction`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            checkout_id: checkout.id,
            payer_wallet: publicKey,
            reference: paymentData.reference,
          }),
        },
      )

      if (!txResponse.ok) {
        // If transaction endpoint doesn't exist, fall back to Solana Pay URL
        // The wallet will parse the solana: URL directly
        await triggerWalletPayment(paymentData.solana_pay_url)
        return
      }

      const txData = await txResponse.json()

      // Sign the transaction with the wallet
      setPaymentStatus('confirming')

      // Get the wallet provider from window
      const provider = getWalletProvider()
      if (!provider) {
        throw new Error('Wallet not available')
      }

      // Deserialize and sign the transaction
      const { Transaction, VersionedTransaction } = await import('@solana/web3.js')

      let transaction
      if (txData.versioned) {
        transaction = VersionedTransaction.deserialize(
          Buffer.from(txData.transaction, 'base64')
        )
      } else {
        transaction = Transaction.from(
          Buffer.from(txData.transaction, 'base64')
        )
      }

      // Sign with wallet
      const signedTx = await provider.signTransaction(transaction)

      // Send the signed transaction back to backend for submission
      const submitResponse = await fetch(
        `${apiBaseUrl}/v1/integrations/solana/submit-transaction`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            checkout_id: checkout.id,
            signed_transaction: Buffer.from(signedTx.serialize()).toString('base64'),
            reference: paymentData.reference,
          }),
        },
      )

      if (!submitResponse.ok) {
        throw new Error('Failed to submit transaction')
      }

      const submitData = await submitResponse.json()
      setTransactionSignature(submitData.signature)

      // Start polling for confirmation
      pollPaymentStatus(paymentData.reference)

    } catch (err: any) {
      console.error('Payment error:', err)

      // Handle user rejection
      if (err.message?.includes('User rejected') || err.code === 4001) {
        setPaymentStatus('idle')
        setError('Transaction was cancelled')
        return
      }

      setPaymentStatus('failed')
      setError(err instanceof Error ? err.message : 'Payment failed')
    }
  }, [connected, publicKey, checkout.id, apiBaseUrl, initiatePayment])

  // Trigger wallet payment using Solana Pay URL (fallback)
  const triggerWalletPayment = useCallback(async (payUrl: string) => {
    const provider = getWalletProvider()
    if (!provider) {
      // Open Solana Pay URL directly - mobile wallets will handle it
      window.location.href = payUrl
      return
    }

    // Parse the Solana Pay URL and construct the transfer
    try {
      const url = new URL(payUrl)
      const recipient = url.pathname.replace('//', '')
      const amount = url.searchParams.get('amount')
      const splToken = url.searchParams.get('spl-token')
      const reference = url.searchParams.get('reference')

      // Import Solana web3.js
      const {
        Connection,
        PublicKey,
        Transaction,
        SystemProgram,
        LAMPORTS_PER_SOL,
      } = await import('@solana/web3.js')

      // Determine RPC endpoint
      const rpcUrl = (window as any).__SOLANA_RPC_URL__ || 'https://api.mainnet-beta.solana.com'
      const connection = new Connection(rpcUrl)

      let transaction: InstanceType<typeof Transaction>

      if (splToken) {
        // SPL Token transfer (USDC, etc.)
        const { getAssociatedTokenAddress, createTransferInstruction, TOKEN_PROGRAM_ID } = await import('@solana/spl-token')

        const senderPubkey = new PublicKey(publicKey!)
        const recipientPubkey = new PublicKey(recipient)
        const mintPubkey = new PublicKey(splToken)

        const senderATA = await getAssociatedTokenAddress(mintPubkey, senderPubkey)
        const recipientATA = await getAssociatedTokenAddress(mintPubkey, recipientPubkey)

        // Amount is in token units (e.g., 1.5 USDC)
        const tokenAmount = Math.floor(parseFloat(amount || '0') * 1_000_000) // USDC has 6 decimals

        transaction = new Transaction().add(
          createTransferInstruction(
            senderATA,
            recipientATA,
            senderPubkey,
            tokenAmount,
            [],
            TOKEN_PROGRAM_ID
          )
        )

        // Add reference for tracking
        if (reference) {
          transaction.add({
            keys: [{ pubkey: new PublicKey(reference), isSigner: false, isWritable: false }],
            programId: new PublicKey('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'),
            data: Buffer.from(''),
          })
        }
      } else {
        // SOL transfer
        const senderPubkey = new PublicKey(publicKey!)
        const recipientPubkey = new PublicKey(recipient)
        const lamports = Math.floor(parseFloat(amount || '0') * LAMPORTS_PER_SOL)

        transaction = new Transaction().add(
          SystemProgram.transfer({
            fromPubkey: senderPubkey,
            toPubkey: recipientPubkey,
            lamports,
          })
        )
      }

      // Get recent blockhash
      const { blockhash } = await connection.getLatestBlockhash()
      transaction.recentBlockhash = blockhash
      transaction.feePayer = new PublicKey(publicKey!)

      setPaymentStatus('confirming')

      // Sign and send
      const signedTx = await provider.signTransaction(transaction)
      const signature = await connection.sendRawTransaction(signedTx.serialize())

      setTransactionSignature(signature)

      // Wait for confirmation
      await connection.confirmTransaction(signature, 'confirmed')

      setPaymentStatus('confirmed')

      // Notify backend of successful payment
      if (reference) {
        pollPaymentStatus(reference)
      }

    } catch (err: any) {
      console.error('Wallet payment error:', err)

      if (err.message?.includes('User rejected') || err.code === 4001) {
        setPaymentStatus('idle')
        return
      }

      setPaymentStatus('failed')
      setError(err.message || 'Transaction failed')
    }
  }, [publicKey])

  // Get wallet provider from window
  const getWalletProvider = () => {
    if (typeof window === 'undefined') return null
    return (window as any).phantom?.solana ||
           (window as any).solflare ||
           (window as any).backpack
  }

  // Poll for payment confirmation
  const pollPaymentStatus = useCallback(
    async (ref: string) => {
      const checkStatus = async () => {
        try {
          const response = await fetch(
            `${apiBaseUrl}/v1/integrations/solana/payment-status?reference=${ref}&expected_amount=${totalAmount}`,
          )

          if (!response.ok) return false

          const data = await response.json()

          if (data.status === 'confirmed') {
            setPaymentStatus('confirmed')

            // Trigger checkout confirmation
            try {
              await confirm({
                payment_processor: 'solana',
                solana_signature: transactionSignature,
                solana_reference: ref,
              })
            } catch (e) {
              console.error('Failed to confirm checkout:', e)
            }

            return true
          } else if (data.status === 'failed') {
            setPaymentStatus('failed')
            setError(data.message)
            return true
          }

          return false
        } catch {
          return false
        }
      }

      // Poll every 2 seconds for up to 5 minutes
      const maxAttempts = 150
      let attempts = 0

      const poll = async () => {
        if (attempts >= maxAttempts) {
          setPaymentStatus('failed')
          setError('Payment confirmation timed out')
          return
        }

        const confirmed = await checkStatus()
        if (!confirmed) {
          attempts++
          setTimeout(poll, 2000)
        }
      }

      poll()
    },
    [totalAmount, apiBaseUrl, confirm, transactionSignature],
  )

  // Handle wallet connection
  const handleConnect = async (walletName: string) => {
    await connect(walletName)
  }

  // Reset state
  const resetPayment = useCallback(() => {
    setPaymentStatus('idle')
    setSolanaPayUrl(null)
    setReference(null)
    setError(null)
    setShowQR(false)
    setTransactionSignature(null)
  }, [])

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col gap-2 text-center">
        <h3 className="text-lg font-semibold">Pay with Crypto</h3>
        <p className="text-sm text-gray-500 dark:text-polar-400">
          Pay {formattedAmount} USDC on Solana
        </p>
      </div>

      {/* Wallet connection + payment */}
      {paymentStatus === 'idle' && !showQR && (
        <div className="flex flex-col gap-4">
          {/* Connected wallet display */}
          {connected && publicKey && (
            <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-polar-800 rounded-lg">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 bg-green-500 rounded-full" />
                <span className="text-sm font-mono">
                  {publicKey.slice(0, 4)}...{publicKey.slice(-4)}
                </span>
              </div>
              <button
                onClick={disconnect}
                className="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              >
                Disconnect
              </button>
            </div>
          )}

          {/* Wallet selection or Pay button */}
          {!connected ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-gray-600 dark:text-polar-400 text-center">
                Connect your wallet to pay
              </p>
              {wallets.map((wallet) => (
                <WalletButton
                  key={wallet.name}
                  wallet={wallet}
                  onSelect={handleConnect}
                  disabled={connecting || disabled || loading}
                />
              ))}
            </div>
          ) : (
            <Button
              onClick={payWithWallet}
              disabled={disabled || loading}
              loading={loading}
              size="lg"
              className="w-full"
            >
              Pay {formattedAmount} USDC
            </Button>
          )}

          {/* QR Code option */}
          <div className="flex items-center gap-4 my-2">
            <div className="flex-1 h-px bg-gray-200 dark:bg-polar-700" />
            <span className="text-xs text-gray-500">or</span>
            <div className="flex-1 h-px bg-gray-200 dark:bg-polar-700" />
          </div>

          <Button
            onClick={async () => {
              await initiatePayment()
              setShowQR(true)
            }}
            disabled={disabled || loading}
            variant="secondary"
            size="lg"
            className="w-full"
          >
            Pay with QR Code (Mobile)
          </Button>

          {/* Info text */}
          <p className="text-xs text-gray-500 dark:text-polar-500 text-center">
            Only 1% platform fee — no credit card fees!
          </p>
        </div>
      )}

      {/* QR Code view */}
      {showQR && solanaPayUrl && paymentStatus !== 'confirmed' && (
        <div className="flex flex-col items-center gap-4">
          <QRCodeDisplay url={solanaPayUrl} size={220} />

          {paymentStatus === 'pending' && (
            <PaymentStatus status="pending" />
          )}

          {/* Copy link */}
          <div className="flex flex-col gap-2 w-full">
            <div className="flex gap-2">
              <input
                type="text"
                value={solanaPayUrl}
                readOnly
                className="flex-1 px-3 py-2 text-xs bg-gray-100 dark:bg-polar-800 rounded border border-gray-200 dark:border-polar-700"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigator.clipboard.writeText(solanaPayUrl)}
              >
                Copy
              </Button>
            </div>
          </div>

          <Button variant="ghost" size="sm" onClick={resetPayment}>
            ← Back to wallet options
          </Button>
        </div>
      )}

      {/* Processing states */}
      {paymentStatus === 'pending' && !showQR && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="pending" />
          <p className="text-sm text-gray-500 dark:text-polar-400 text-center">
            Please confirm the transaction in your wallet...
          </p>
        </div>
      )}

      {paymentStatus === 'confirming' && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="confirming" />
          <p className="text-sm text-gray-500 dark:text-polar-400 text-center">
            Transaction sent! Waiting for confirmation...
          </p>
          {transactionSignature && (
            <a
              href={`https://solscan.io/tx/${transactionSignature}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-500 hover:underline"
            >
              View on Solscan ↗
            </a>
          )}
        </div>
      )}

      {paymentStatus === 'confirmed' && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="confirmed" />
          <p className="text-sm text-gray-500 dark:text-polar-400 text-center">
            Thank you! Your order is being processed.
          </p>
          {transactionSignature && (
            <a
              href={`https://solscan.io/tx/${transactionSignature}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-500 hover:underline"
            >
              View transaction ↗
            </a>
          )}
        </div>
      )}

      {paymentStatus === 'failed' && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="failed" message={error || undefined} />
          <Button onClick={resetPayment}>Try Again</Button>
        </div>
      )}

      {/* Footer */}
      <div className="border-t border-gray-200 dark:border-polar-700 pt-4">
        <p className="text-xs text-gray-500 dark:text-polar-500 text-center">
          Powered by Solana. Transactions are final.
        </p>
      </div>
    </div>
  )
}

// Wrapper component with provider
export const SolanaCheckoutForm = (props: SolanaCheckoutFormProps) => {
  return (
    <SolanaWalletProvider autoConnect>
      <SolanaCheckoutFormInner {...props} />
    </SolanaWalletProvider>
  )
}

export default SolanaCheckoutForm

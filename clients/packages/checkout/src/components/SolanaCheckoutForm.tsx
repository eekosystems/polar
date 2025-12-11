'use client'

import type { CheckoutPublic } from '@polar-sh/sdk/models/components/checkoutpublic'
import type { CheckoutPublicConfirmed } from '@polar-sh/sdk/models/components/checkoutpublicconfirmed'
import type { CheckoutUpdatePublic } from '@polar-sh/sdk/models/components/checkoutupdatepublic'
import Button from '@polar-sh/ui/components/atoms/Button'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { UseFormReturn } from 'react-hook-form'
import { formatCurrencyNumber } from '../utils/money'

// Solana Pay URL QR code display
const QRCodeDisplay = ({ url, size = 200 }: { url: string; size?: number }) => {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)

  useEffect(() => {
    // Generate QR code using a simple API
    // In production, use a library like 'qrcode' or '@solana/pay'
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
        Scan with your Solana wallet
      </p>
    </div>
  )
}

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

// Wallet icon components
const PhantomIcon = () => (
  <svg viewBox="0 0 128 128" className="w-5 h-5">
    <circle cx="64" cy="64" r="64" fill="#AB9FF2" />
    <path
      d="M110.584 64.9142H99.142C99.142 41.7651 80.173 23 56.7724 23C33.6612 23 14.8716 41.3057 14.4118 64.0583C13.936 87.5148 35.1517 107.5 58.8402 107.5H63.9553C84.2847 107.5 110.584 89.1089 110.584 64.9142Z"
      fill="url(#paint0_linear)"
    />
    <defs>
      <linearGradient
        id="paint0_linear"
        x1="62.4982"
        y1="107.5"
        x2="62.4982"
        y2="23"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#534BB1" />
        <stop offset="1" stopColor="#551BF9" />
      </linearGradient>
    </defs>
  </svg>
)

const SolflareIcon = () => (
  <svg viewBox="0 0 101 88" className="w-5 h-5">
    <path
      d="M100.48 69.38L83.4 87.49C82.84 88.07 82.07 88.4 81.26 88.4H5.25C2.88 88.4 1.59 85.61 3.13 83.89L20.21 65.77C20.76 65.2 21.53 64.87 22.33 64.87H98.34C100.71 64.87 102.01 67.66 100.48 69.38Z"
      fill="url(#paint1_linear)"
    />
    <defs>
      <linearGradient
        id="paint1_linear"
        x1="10"
        y1="88"
        x2="100"
        y2="65"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#FFC10B" />
        <stop offset="1" stopColor="#FB3F2E" />
      </linearGradient>
    </defs>
  </svg>
)

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

export const SolanaCheckoutForm = ({
  checkout,
  loading,
  disabled,
  apiBaseUrl = '',
}: SolanaCheckoutFormProps) => {
  const [paymentStatus, setPaymentStatus] = useState<
    'idle' | 'pending' | 'confirming' | 'confirmed' | 'failed'
  >('idle')
  const [solanaPayUrl, setSolanaPayUrl] = useState<string | null>(null)
  const [reference, setReference] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const totalAmount = checkout.totalAmount || 0
  const formattedAmount = useMemo(
    () => formatCurrencyNumber(totalAmount, checkout.currency || 'usd', 2),
    [totalAmount, checkout.currency],
  )

  // Create Solana payment request
  const createPaymentRequest = useCallback(async () => {
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
          }),
        },
      )

      if (!response.ok) {
        throw new Error('Failed to create payment request')
      }

      const data = await response.json()
      setSolanaPayUrl(data.solana_pay_url)
      setReference(data.reference)

      // Start polling for payment confirmation
      pollPaymentStatus(data.reference)
    } catch (err) {
      setPaymentStatus('failed')
      setError(err instanceof Error ? err.message : 'Failed to create payment')
    }
  }, [checkout.id, apiBaseUrl])

  // Poll for payment status
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

      // Poll every 3 seconds for up to 30 minutes
      const maxAttempts = 600
      let attempts = 0

      const poll = async () => {
        if (attempts >= maxAttempts) {
          setPaymentStatus('failed')
          setError('Payment timed out')
          return
        }

        const confirmed = await checkStatus()
        if (!confirmed) {
          attempts++
          setTimeout(poll, 3000)
        }
      }

      poll()
    },
    [totalAmount, apiBaseUrl],
  )

  // Reset state
  const resetPayment = useCallback(() => {
    setPaymentStatus('idle')
    setSolanaPayUrl(null)
    setReference(null)
    setError(null)
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

      {/* Payment flow based on status */}
      {paymentStatus === 'idle' && (
        <div className="flex flex-col gap-4">
          {/* Wallet buttons */}
          <div className="flex flex-col gap-3">
            <Button
              onClick={createPaymentRequest}
              disabled={disabled || loading}
              loading={loading}
              size="lg"
              className="w-full flex items-center justify-center gap-2"
            >
              <PhantomIcon />
              Pay with Phantom
            </Button>

            <Button
              onClick={createPaymentRequest}
              disabled={disabled || loading}
              variant="secondary"
              size="lg"
              className="w-full flex items-center justify-center gap-2"
            >
              <SolflareIcon />
              Pay with Solflare
            </Button>

            <Button
              onClick={createPaymentRequest}
              disabled={disabled || loading}
              variant="secondary"
              size="lg"
              className="w-full"
            >
              Show QR Code
            </Button>
          </div>

          {/* Info text */}
          <p className="text-xs text-gray-500 dark:text-polar-500 text-center">
            Payment is processed on Solana. You&apos;ll pay in USDC.
            <br />
            Only 1% platform fee - no credit card fees!
          </p>
        </div>
      )}

      {paymentStatus === 'pending' && solanaPayUrl && (
        <div className="flex flex-col items-center gap-4">
          <QRCodeDisplay url={solanaPayUrl} size={220} />
          <PaymentStatus status="pending" />

          {/* Manual payment link */}
          <div className="flex flex-col gap-2 w-full">
            <p className="text-xs text-gray-500 dark:text-polar-500 text-center">
              Or copy the payment link:
            </p>
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
            Cancel
          </Button>
        </div>
      )}

      {paymentStatus === 'confirming' && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="confirming" />
          <p className="text-sm text-gray-500 dark:text-polar-400">
            Your transaction has been detected and is being confirmed...
          </p>
        </div>
      )}

      {paymentStatus === 'confirmed' && (
        <div className="flex flex-col items-center gap-4">
          <PaymentStatus status="confirmed" />
          <p className="text-sm text-gray-500 dark:text-polar-400">
            Thank you! Your order is being processed.
          </p>
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
          Powered by Solana Pay. Transactions are final and non-refundable.
        </p>
      </div>
    </div>
  )
}

export default SolanaCheckoutForm

import { useCallback, useEffect, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { MarketDepthPanel } from '@/components/trading/MarketDepthPanel'
import { QuoteHeader } from '@/components/trading/QuoteHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useLiveQuote } from '@/hooks/useLiveQuote'
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { showToast } from '@/utils/toast'

const PRICE_TYPES = [
  { value: 'MARKET', label: 'Market' },
  { value: 'LIMIT', label: 'Limit' },
  { value: 'SL-M', label: 'SL-M' },
  { value: 'SL', label: 'SL-L' },
] as const

const FNO_PRODUCT_TYPES = [
  { value: 'NRML', label: 'NRML' },
  { value: 'MIS', label: 'MIS' },
] as const

const EQUITY_PRODUCT_TYPES = [
  { value: 'CNC', label: 'CNC' },
  { value: 'MIS', label: 'MIS' },
] as const

interface SearchResult {
  symbol: string
  brsymbol: string
  name: string
  exchange: string
  token: string
  lotsize: number
  tick_size: number
  instrumenttype: string
}

function roundToTick(price: number, tickSize: number): number {
  if (tickSize <= 0) return price
  return Number((Math.round(price / tickSize) * tickSize).toFixed(2))
}

function adjustPrice(price: number, tickSize: number, direction: 'up' | 'down'): number {
  const rounded = roundToTick(price, tickSize)
  if (direction === 'up') {
    return Number((rounded + tickSize).toFixed(2))
  }
  return Math.max(0, Number((rounded - tickSize).toFixed(2)))
}

function isFnOExchange(exchange: string): boolean {
  return ['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO'].includes(exchange)
}

export default function PlaceOrder() {
  const { apiKey } = useAuthStore()
  const { tradingExchanges, defaultExchange } = useSupportedExchanges()

  const [exchange, setExchange] = useState(defaultExchange)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showResults, setShowResults] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<SearchResult | null>(null)

  const [formAction, setFormAction] = useState<'BUY' | 'SELL'>('BUY')
  const [formQuantity, setFormQuantity] = useState(1)
  const [formPriceType, setFormPriceType] = useState<string>('MARKET')
  const [formProduct, setFormProduct] = useState<string>('CNC')
  const [formPrice, setFormPrice] = useState(0)
  const [formTriggerPrice, setFormTriggerPrice] = useState(0)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isDepthExpanded, setIsDepthExpanded] = useState(false)
  const [quantityMode, setQuantityMode] = useState<'lots' | 'shares'>('lots')
  const [lotMultiplier, setLotMultiplier] = useState(1)

  const searchRef = useRef<HTMLDivElement>(null)

  const symbol = selectedSymbol?.symbol ?? ''
  const lotSize = selectedSymbol?.lotsize ?? 1
  const tickSize = selectedSymbol?.tick_size ?? 0.05

  const productTypes = isFnOExchange(exchange) ? FNO_PRODUCT_TYPES : EQUITY_PRODUCT_TYPES

  const {
    data: liveData,
    isLoading: isLoadingQuotes,
    isConnected,
  } = useLiveQuote(symbol, exchange, {
    enabled: !!symbol && !!exchange,
    mode: 'Depth',
    useQuotesFallback: true,
    useDepthFallback: true,
  })

  const mergedData = {
    ltp: liveData.ltp,
    close: liveData.close,
    change: liveData.change,
    change_percent: liveData.changePercent,
    bidPrice: liveData.bidPrice,
    askPrice: liveData.askPrice,
    bidSize: liveData.bidSize,
    askSize: liveData.askSize,
    depth: liveData.depth,
  }

  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      setSearchResults([])
      return
    }
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          `/search/api/search?q=${encodeURIComponent(searchQuery)}&exchange=${exchange}`
        )
        const data = await res.json()
        setSearchResults(data.results?.slice(0, 20) ?? [])
        setShowResults(true)
      } catch {
        setSearchResults([])
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [searchQuery, exchange])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowResults(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    setSelectedSymbol(null)
    setSearchQuery('')
    setSearchResults([])
    setShowResults(false)
    setFormQuantity(1)
    setFormPrice(0)
    setFormTriggerPrice(0)
    setLotMultiplier(1)
    setQuantityMode('lots')
    const isFnO = isFnOExchange(exchange)
    setFormProduct(isFnO ? 'NRML' : 'CNC')
  }, [exchange])

  useEffect(() => {
    if (selectedSymbol) {
      setFormQuantity(lotSize)
      setLotMultiplier(1)
      setQuantityMode('lots')
      setFormPrice(0)
      setFormTriggerPrice(0)
      const isFnO = isFnOExchange(selectedSymbol.exchange)
      setFormProduct(isFnO ? 'NRML' : 'CNC')
    }
  }, [selectedSymbol, lotSize])

  useEffect(() => {
    if (formPriceType !== 'MARKET' && mergedData.ltp && formPrice === 0) {
      setFormPrice(roundToTick(mergedData.ltp, tickSize))
    }
  }, [formPriceType, mergedData.ltp, formPrice, tickSize])

  const needsPrice = formPriceType === 'LIMIT' || formPriceType === 'SL'
  const needsTrigger = formPriceType === 'SL-M' || formPriceType === 'SL'

  const isValid = useCallback(() => {
    if (!symbol || !exchange) return false
    if (!apiKey) return false
    if (formQuantity <= 0) return false
    if (needsPrice && formPrice <= 0) return false
    if (needsTrigger && formTriggerPrice <= 0) return false
    return true
  }, [symbol, exchange, apiKey, formQuantity, needsPrice, formPrice, needsTrigger, formTriggerPrice])

  const handleSelectSymbol = (result: SearchResult) => {
    setSelectedSymbol(result)
    setSearchQuery(result.symbol)
    setShowResults(false)
  }

  const handleQuantityChange = (value: string) => {
    const num = parseInt(value, 10) || 0
    if (quantityMode === 'lots') {
      setFormQuantity(num * lotSize)
      setLotMultiplier(num)
    } else {
      const roundedQty = Math.max(lotSize, Math.round(num / lotSize) * lotSize)
      setFormQuantity(roundedQty)
      setLotMultiplier(roundedQty / lotSize)
    }
  }

  const handleSubmit = async () => {
    if (!isValid()) {
      showToast.error('Please fill all required fields')
      return
    }
    if (!apiKey) {
      showToast.error('API key not found. Please set up your API key.')
      return
    }

    setIsSubmitting(true)
    try {
      const apiPriceType = formPriceType as 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
      const orderRequest = {
        apikey: apiKey,
        strategy: 'Manual',
        exchange,
        symbol,
        action: formAction,
        quantity: formQuantity,
        pricetype: apiPriceType,
        product: formProduct as 'CNC' | 'NRML' | 'MIS',
        ...(needsPrice && { price: formPrice }),
        ...(needsTrigger && { trigger_price: formTriggerPrice }),
      }

      const response = await tradingApi.placeOrder(orderRequest)
      const orderid = (response as unknown as { orderid?: string }).orderid
      if (response.status === 'success' && orderid) {
        showToast.success(`Order placed: ${orderid}`)
        setSelectedSymbol(null)
        setSearchQuery('')
        setFormQuantity(lotSize)
        setFormPrice(0)
        setFormTriggerPrice(0)
        setLotMultiplier(1)
      } else {
        const errorMsg = response.message || 'Order placement failed'
        showToast.error(errorMsg)
      }
    } catch (err: unknown) {
      let errorMsg = 'Order placement failed'
      if (err && typeof err === 'object') {
        const axiosError = err as { response?: { data?: { message?: string } }; message?: string }
        if (axiosError.response?.data?.message) {
          errorMsg = axiosError.response.data.message
        } else if (axiosError.message) {
          errorMsg = axiosError.message
        }
      }
      showToast.error(errorMsg)
    } finally {
      setIsSubmitting(false)
    }
  }

  const displayQuantity = quantityMode === 'lots' ? lotMultiplier : formQuantity
  const isLoading = isLoadingQuotes && !mergedData.ltp && !isConnected

  return (
    <div className="container mx-auto p-4 space-y-4 max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>Place Order</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label className="text-xs">Exchange</Label>
            <Select value={exchange} onValueChange={setExchange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {tradingExchanges.map((ex) => (
                  <SelectItem key={ex.value} value={ex.value}>
                    {ex.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2 relative" ref={searchRef}>
            <Label className="text-xs">Symbol</Label>
            <Input
              placeholder="Search symbol..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                if (selectedSymbol && e.target.value !== selectedSymbol.symbol) {
                  setSelectedSymbol(null)
                }
              }}
              onFocus={() => searchResults.length > 0 && setShowResults(true)}
            />
            {showResults && searchResults.length > 0 && (
              <div className="absolute z-50 w-full mt-1 bg-popover border rounded-md shadow-lg max-h-60 overflow-auto">
                {searchResults.map((result, idx) => (
                  <button
                    key={`${result.symbol}-${result.exchange}-${idx}`}
                    type="button"
                    className="w-full px-3 py-2 text-left hover:bg-accent flex items-center justify-between gap-2"
                    onClick={() => handleSelectSymbol(result)}
                  >
                    <div>
                      <span className="font-medium">{result.symbol}</span>
                      {result.name && (
                        <span className="text-muted-foreground text-xs ml-2">{result.name}</span>
                      )}
                    </div>
                    <span className="text-xs text-muted-foreground">{result.exchange}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {selectedSymbol && (
        <Card>
          <CardContent className="space-y-4 pt-4">
            <QuoteHeader
              exchange={exchange}
              ltp={mergedData.ltp}
              prevClose={mergedData.close}
              change={mergedData.change}
              changePercent={mergedData.change_percent}
              bidPrice={mergedData.bidPrice}
              askPrice={mergedData.askPrice}
              bidSize={mergedData.bidSize}
              askSize={mergedData.askSize}
              isLoading={isLoading}
            />

            <MarketDepthPanel
              depth={mergedData.depth}
              isExpanded={isDepthExpanded}
              onToggle={() => setIsDepthExpanded(!isDepthExpanded)}
              maxLevels={5}
            />

            <div className="space-y-2">
              <Label className="text-xs">Action</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={formAction === 'BUY' ? 'default' : 'outline'}
                  className={cn('flex-1', formAction === 'BUY' && 'bg-green-600 hover:bg-green-700')}
                  onClick={() => setFormAction('BUY')}
                >
                  BUY
                </Button>
                <Button
                  type="button"
                  variant={formAction === 'SELL' ? 'default' : 'outline'}
                  className={cn('flex-1', formAction === 'SELL' && 'bg-red-600 hover:bg-red-700')}
                  onClick={() => setFormAction('SELL')}
                >
                  SELL
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs">Quantity</Label>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => setQuantityMode('lots')}
                    className={cn(
                      'px-2 py-0.5 text-[10px] rounded',
                      quantityMode === 'lots'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground hover:bg-muted/80'
                    )}
                  >
                    Lots
                  </button>
                  <button
                    type="button"
                    onClick={() => setQuantityMode('shares')}
                    className={cn(
                      'px-2 py-0.5 text-[10px] rounded',
                      quantityMode === 'shares'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground hover:bg-muted/80'
                    )}
                  >
                    Shares
                  </button>
                </div>
              </div>
              <Input
                type="number"
                value={displayQuantity}
                onChange={(e) => handleQuantityChange(e.target.value)}
                min={1}
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Lot size: {lotSize}</span>
                <span>Total qty: {formQuantity}</span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label className="text-xs">Price Type</Label>
                <Select value={formPriceType} onValueChange={setFormPriceType}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PRICE_TYPES.map((pt) => (
                      <SelectItem key={pt.value} value={pt.value}>
                        {pt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="text-xs">Product</Label>
                <Select value={formProduct} onValueChange={setFormProduct}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {productTypes.map((pt) => (
                      <SelectItem key={pt.value} value={pt.value}>
                        {pt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {needsPrice && (
              <div className="space-y-2">
                <Label className="text-xs">Price</Label>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="px-2"
                    onClick={() => setFormPrice(adjustPrice(formPrice, tickSize, 'down'))}
                  >
                    -
                  </Button>
                  <Input
                    type="number"
                    value={formPrice}
                    onChange={(e) => setFormPrice(parseFloat(e.target.value) || 0)}
                    onBlur={() => setFormPrice(roundToTick(formPrice, tickSize))}
                    className="flex-1 text-center"
                    step={tickSize}
                    min={0}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="px-2"
                    onClick={() => setFormPrice(adjustPrice(formPrice, tickSize, 'up'))}
                  >
                    +
                  </Button>
                </div>
                <p className="text-[10px] text-muted-foreground">Tick size: {tickSize}</p>
              </div>
            )}

            {needsTrigger && (
              <div className="space-y-2">
                <Label className="text-xs">Trigger Price</Label>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="px-2"
                    onClick={() => setFormTriggerPrice(adjustPrice(formTriggerPrice, tickSize, 'down'))}
                  >
                    -
                  </Button>
                  <Input
                    type="number"
                    value={formTriggerPrice}
                    onChange={(e) => setFormTriggerPrice(parseFloat(e.target.value) || 0)}
                    onBlur={() => setFormTriggerPrice(roundToTick(formTriggerPrice, tickSize))}
                    className="flex-1 text-center"
                    step={tickSize}
                    min={0}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="px-2"
                    onClick={() => setFormTriggerPrice(adjustPrice(formTriggerPrice, tickSize, 'up'))}
                  >
                    +
                  </Button>
                </div>
              </div>
            )}

            <Button
              className={cn(
                'w-full',
                formAction === 'BUY'
                  ? 'bg-green-600 hover:bg-green-700'
                  : 'bg-red-600 hover:bg-red-700'
              )}
              disabled={!isValid() || isSubmitting}
              onClick={handleSubmit}
            >
              {isSubmitting ? 'Placing...' : `Place ${formAction} Order`}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

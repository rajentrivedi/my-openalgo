import {
  Bell,
  BellOff,
  BellRing,
  ExternalLink,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { wishlistApi, type WishlistAlert, type WishlistItem } from '@/api/wishlist'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'

interface SearchResult {
  symbol: string
  brsymbol: string
  name: string
  exchange: string
  brexchange: string
  token: string
  expiry?: string
  strike?: number
  lotsize?: number
  freeze_qty?: number
}

const EXCHANGES = ['NSE', 'BSE', 'NFO', 'CDS', 'MCX']

const CONDITION_LABELS: Record<string, string> = {
  target_above: 'Price Above',
  target_below: 'Price Below',
  percent_up: '% Up',
  percent_down: '% Down',
}

export default function Wishlist() {
  const [items, setItems] = useState<WishlistItem[]>([])
  const [isLoading, setIsLoading] = useState(true)

  const [symbol, setSymbol] = useState('')
  const [exchange, setExchange] = useState('NSE')
  const [notes, setNotes] = useState('')
  const [targetPrice, setTargetPrice] = useState('')
  const [isAdding, setIsAdding] = useState(false)

  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  const searchRef = useRef<HTMLDivElement>(null)

  // Alert state
  const [alerts, setAlerts] = useState<WishlistAlert[]>([])
  const [alertModalItem, setAlertModalItem] = useState<WishlistItem | null>(null)
  const [alertCondition, setAlertCondition] = useState('target_above')
  const [alertValue, setAlertValue] = useState('')
  const [isCreatingAlert, setIsCreatingAlert] = useState(false)

  const fetchItems = useCallback(async () => {
    try {
      const data = await wishlistApi.getItems()
      setItems(data)
    } catch (_error) {
      showToast.error('Failed to load wishlist', 'actionCenter')
    } finally {
      setIsLoading(false)
    }
  }, [])

  const fetchAlerts = useCallback(async () => {
    try {
      const data = await wishlistApi.getAlerts()
      setAlerts(data)
    } catch {
      // silent fail
    }
  }, [])

  useEffect(() => {
    fetchItems()
    fetchAlerts()
  }, [fetchItems, fetchAlerts])

  const alertsByItemId = useMemo(() => {
    const map = new Map<number, WishlistAlert[]>()
    for (const alert of alerts) {
      const existing = map.get(alert.wishlist_item_id)
      if (existing) {
        existing.push(alert)
      } else {
        map.set(alert.wishlist_item_id, [alert])
      }
    }
    return map
  }, [alerts])

  const performAutocompleteSearch = useCallback(async (query: string, exch: string) => {
    if (query.length < 2) {
      setSearchResults([])
      setShowDropdown(false)
      return
    }

    try {
      const params = new URLSearchParams()
      params.append('q', query)
      if (exch) params.append('exchange', exch)

      const response = await fetch(`/search/api/search?${params}`, {
        credentials: 'include',
      })
      const data = await response.json()
      setSearchResults((data.results || []).slice(0, 10))
      setShowDropdown(true)
    } catch (_error) {
      setSearchResults([])
    }
  }, [])

  useEffect(() => {
    if (!selectedSymbol && symbol.length >= 2) {
      const timer = setTimeout(() => {
        performAutocompleteSearch(symbol, exchange)
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [symbol, exchange, selectedSymbol, performAutocompleteSearch])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [])

  const handleResultClick = (result: SearchResult) => {
    setSymbol(result.symbol)
    setExchange(result.exchange)
    setSelectedSymbol(result.symbol)
    setShowDropdown(false)
  }

  const handleSymbolChange = (value: string) => {
    setSymbol(value)
    if (value !== selectedSymbol) {
      setSelectedSymbol(null)
    }
  }

  const handleAdd = async () => {
    if (!symbol.trim() || !selectedSymbol) return

    setIsAdding(true)
    try {
      await wishlistApi.addItem({
        symbol: symbol.trim(),
        exchange,
        notes: notes.trim() || undefined,
        target_price: targetPrice ? parseFloat(targetPrice) : undefined,
      })
      setSymbol('')
      setExchange('NSE')
      setNotes('')
      setTargetPrice('')
      setSelectedSymbol(null)
      setSearchResults([])
      await fetchItems()
      showToast.success('Symbol added to wishlist', 'actionCenter')
    } catch (_error) {
      showToast.error('Failed to add symbol', 'actionCenter')
    } finally {
      setIsAdding(false)
    }
  }

  const handleRemove = async (itemId: number) => {
    try {
      await wishlistApi.deleteAlertsForItem(itemId)
      await wishlistApi.removeItem(itemId)
      await fetchItems()
      await fetchAlerts()
      showToast.success('Symbol removed from wishlist', 'actionCenter')
    } catch (_error) {
      showToast.error('Failed to remove symbol', 'actionCenter')
    }
  }

  const openTradingView = (symbol: string, exchange: string) => {
    window.open(
      `https://www.tradingview.com/chart/?symbol=${exchange}:${symbol}`,
      '_blank',
      'noopener,noreferrer'
    )
  }

  const liveSymbols = useMemo(
    () => items.map((item) => ({ symbol: item.symbol, exchange: item.exchange })),
    [items]
  )
  const { data: marketData } = useMarketData({
    symbols: liveSymbols,
    mode: 'Quote',
    enabled: items.length > 0,
  })

  const getLivePrice = useCallback(
    (symbol: string, exchange: string) => {
      const key = `${exchange}:${symbol}`
      return marketData.get(key)?.data
    },
    [marketData]
  )

  const handleRefresh = async () => {
    setIsLoading(true)
    await fetchItems()
    await fetchAlerts()
    showToast.success('Wishlist refreshed', 'actionCenter')
  }

  const handleCreateAlert = async () => {
    if (!alertModalItem || !alertValue) return

    setIsCreatingAlert(true)
    try {
      await wishlistApi.createAlert({
        wishlist_item_id: alertModalItem.id,
        condition_type: alertCondition,
        condition_value: parseFloat(alertValue),
      })
      setAlertModalItem(null)
      setAlertCondition('target_above')
      setAlertValue('')
      await fetchAlerts()
      showToast.success('Alert created', 'actionCenter')
    } catch (_error) {
      showToast.error('Failed to create alert', 'actionCenter')
    } finally {
      setIsCreatingAlert(false)
    }
  }

  const handleToggleAlert = async (alertId: number) => {
    try {
      await wishlistApi.toggleAlert(alertId)
      await fetchAlerts()
    } catch {
      showToast.error('Failed to toggle alert', 'actionCenter')
    }
  }

  const handleDeleteAlert = async (alertId: number) => {
    try {
      await wishlistApi.deleteAlert(alertId)
      await fetchAlerts()
      showToast.success('Alert deleted', 'actionCenter')
    } catch {
      showToast.error('Failed to delete alert', 'actionCenter')
    }
  }

  const getAlertIcon = (itemAlerts: WishlistAlert[] | undefined) => {
    if (!itemAlerts || itemAlerts.length === 0) return null

    const hasActive = itemAlerts.some((a) => a.is_active && !a.triggered)
    const hasTriggered = itemAlerts.some((a) => a.triggered)

    if (hasActive) {
      return <Bell className="h-4 w-4 text-green-500" />
    }
    if (hasTriggered) {
      return <BellRing className="h-4 w-4 text-amber-500" />
    }
    return <BellOff className="h-4 w-4 text-muted-foreground" />
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  return (
    <div className="py-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Wishlist</h1>
          <p className="text-muted-foreground text-sm">
            Save symbols you want to track or trade later
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add Symbol</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="relative" ref={searchRef}>
              <Input
                placeholder="Search symbol (e.g., RELIANCE)"
                value={symbol}
                onChange={(e) => handleSymbolChange(e.target.value)}
                onFocus={() => {
                  if (symbol.length >= 2 && searchResults.length > 0) {
                    setShowDropdown(true)
                  }
                }}
                autoComplete="off"
              />

              {showDropdown && searchResults.length > 0 && (
                <div className="absolute z-50 w-full mt-1 bg-background border rounded-lg shadow-lg max-h-60 overflow-y-auto">
                  {searchResults.map((result, index) => (
                    <div
                      key={index}
                      className="p-2.5 border-b last:border-b-0 hover:bg-muted cursor-pointer flex items-center justify-between gap-2"
                      onClick={() => handleResultClick(result)}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-sm truncate">{result.symbol}</div>
                        <div className="text-xs text-muted-foreground truncate">
                          {result.name}
                        </div>
                      </div>
                      <span className="shrink-0 bg-primary text-primary-foreground px-2 py-0.5 rounded text-xs font-semibold">
                        {result.exchange}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {showDropdown && searchResults.length === 0 && symbol.length >= 2 && (
                <div className="absolute z-50 w-full mt-1 bg-background border rounded-lg shadow-lg p-6 text-center">
                  <Search className="h-8 w-8 mx-auto text-muted-foreground/50 mb-2" />
                  <div className="text-sm font-medium">No results found</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    Try a different search term
                  </div>
                </div>
              )}
            </div>

            <div>
              <Select value={exchange} onValueChange={setExchange}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select exchange" />
                </SelectTrigger>
                <SelectContent>
                  {EXCHANGES.map((ex) => (
                    <SelectItem key={ex} value={ex}>
                      {ex}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Input
              placeholder="Notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />

            <Input
              type="number"
              step="any"
              placeholder="Target price (optional)"
              value={targetPrice}
              onChange={(e) => setTargetPrice(e.target.value)}
            />

            <Button
              className="w-full"
              onClick={handleAdd}
              disabled={isAdding || !symbol.trim() || selectedSymbol === null}
            >
              <Plus className="h-4 w-4 mr-2" />
              {isAdding ? 'Adding...' : 'Add to Wishlist'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Saved Symbols</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[20px]" />
                  <TableHead className="w-[30px]">Alert</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">Chg%</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead className="text-right">Target</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead className="w-[60px]">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={10} className="text-center text-muted-foreground py-8">
                      No symbols in your wishlist yet
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((item) => {
                    const live = getLivePrice(item.symbol, item.exchange)
                    const ltp = live?.ltp
                    const changePercent = live?.change_percent
                    const isUp = changePercent != null && changePercent >= 0
                    const itemAlerts = alertsByItemId.get(item.id)
                    const hasAlert = itemAlerts && itemAlerts.length > 0

                    return (
                      <TableRow key={item.id}>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => openTradingView(item.symbol, item.exchange)}
                            title={`Open ${item.symbol} on TradingView`}
                          >
                            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                          </Button>
                        </TableCell>

                        <TableCell>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Dialog
                                  open={alertModalItem?.id === item.id}
                                  onOpenChange={(open) => {
                                    if (!open) setAlertModalItem(null)
                                  }}
                                >
                                  <DialogTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="h-7 w-7"
                                      onClick={() => setAlertModalItem(item)}
                                    >
                                      {getAlertIcon(itemAlerts)}
                                    </Button>
                                  </DialogTrigger>

                                  <DialogContent className="max-w-sm">
                                    <DialogHeader>
                                      <DialogTitle>
                                        Alert for {item.symbol} ({item.exchange})
                                      </DialogTitle>
                                    </DialogHeader>

                                    <div className="space-y-4 pt-2">
                                      {hasAlert ? (
                                        <div className="space-y-3">
                                          {itemAlerts!.map((alert) => (
                                            <div
                                              key={alert.id}
                                              className="flex items-center justify-between gap-2 p-3 border rounded-md"
                                            >
                                              <div className="flex-1 min-w-0">
                                                <div className="text-sm font-medium">
                                                  {CONDITION_LABELS[alert.condition_type]}{' '}
                                                  {alert.condition_value}
                                                  {alert.condition_type === 'percent_up' || alert.condition_type === 'percent_down'
                                                    ? '%'
                                                    : ''}
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                  {alert.triggered
                                                    ? `Triggered at ₹${alert.last_checked_price}`
                                                    : alert.is_active
                                                      ? 'Active'
                                                      : 'Disabled'}
                                                </div>
                                              </div>
                                              <div className="flex items-center gap-2">
                                                <Switch
                                                  checked={alert.is_active && !alert.triggered}
                                                  onCheckedChange={() => handleToggleAlert(alert.id)}
                                                  disabled={alert.triggered}
                                                />
                                                <Button
                                                  variant="ghost"
                                                  size="icon"
                                                  className="h-7 w-7 text-destructive"
                                                  onClick={() => handleDeleteAlert(alert.id)}
                                                >
                                                  <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
                                              </div>
                                            </div>
                                          ))}

                                          <Button
                                            variant="outline"
                                            size="sm"
                                            className="w-full"
                                            onClick={() => {
                                              setAlertCondition('target_above')
                                              setAlertValue('')
                                            }}
                                          >
                                            <Bell className="h-4 w-4 mr-2" />
                                            Add Another Alert
                                          </Button>
                                        </div>
                                      ) : null}

                                      <div className="space-y-3">
                                        <div>
                                          <label className="text-sm font-medium mb-1 block">
                                            Condition
                                          </label>
                                          <Select
                                            value={alertCondition}
                                            onValueChange={setAlertCondition}
                                          >
                                            <SelectTrigger>
                                              <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                              <SelectItem value="target_above">
                                                Price Above
                                              </SelectItem>
                                              <SelectItem value="target_below">
                                                Price Below
                                              </SelectItem>
                                              <SelectItem value="percent_up">% Up</SelectItem>
                                              <SelectItem value="percent_down">% Down</SelectItem>
                                            </SelectContent>
                                          </Select>
                                        </div>

                                        <div>
                                          <label className="text-sm font-medium mb-1 block">
                                            {alertCondition.startsWith('percent')
                                              ? 'Percentage (%)'
                                              : 'Target Price (₹)'}
                                          </label>
                                          <Input
                                            type="number"
                                            step="any"
                                            placeholder={
                                              alertCondition.startsWith('percent')
                                                ? 'e.g., 5'
                                                : 'e.g., 2500'
                                            }
                                            value={alertValue}
                                            onChange={(e) => setAlertValue(e.target.value)}
                                          />
                                        </div>

                                        <Button
                                          className="w-full"
                                          onClick={handleCreateAlert}
                                          disabled={
                                            isCreatingAlert || !alertValue || parseFloat(alertValue) <= 0
                                          }
                                        >
                                          {isCreatingAlert ? 'Creating...' : 'Set Alert'}
                                        </Button>
                                      </div>
                                    </div>
                                  </DialogContent>
                                </Dialog>
                              </TooltipTrigger>
                              <TooltipContent side="right">
                                {hasAlert
                                  ? 'Manage alerts'
                                  : 'Set price alert'}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </TableCell>

                        <TableCell>
                          <button
                            onClick={() => openTradingView(item.symbol, item.exchange)}
                            className="font-medium hover:text-primary hover:underline transition-colors text-left cursor-pointer"
                            title="View on TradingView"
                          >
                            {item.symbol}
                          </button>
                        </TableCell>
                        <TableCell>
                          <span className="bg-primary text-primary-foreground px-2 py-0.5 rounded text-xs font-semibold">
                            {item.exchange}
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {ltp != null
                            ? `\u20B9${Number(ltp).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                            : '\u2014'}
                        </TableCell>
                        <TableCell className={`text-right tabular-nums ${isUp ? 'text-green-600' : changePercent != null ? 'text-red-600' : ''}`}>
                          {changePercent != null
                            ? `${isUp ? '+' : ''}${changePercent.toFixed(2)}%`
                            : '\u2014'}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {item.notes || '\u2014'}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground tabular-nums">
                          {item.target_price != null
                            ? `\u20B9${Number(item.target_price).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
                            : '\u2014'}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {item.created_at
                            ? new Date(item.created_at).toLocaleDateString('en-IN', {
                                day: 'numeric',
                                month: 'short',
                                year: 'numeric',
                              })
                            : '\u2014'}
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive hover:text-destructive h-8 w-8"
                            onClick={() => handleRemove(item.id)}
                            aria-label={`Remove ${item.symbol}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

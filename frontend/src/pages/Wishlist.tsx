import { Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { wishlistApi, type WishlistItem } from '@/api/wishlist'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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

export default function Wishlist() {
  const [items, setItems] = useState<WishlistItem[]>([])
  const [isLoading, setIsLoading] = useState(true)

  // Add form state
  const [symbol, setSymbol] = useState('')
  const [exchange, setExchange] = useState('NSE')
  const [notes, setNotes] = useState('')
  const [targetPrice, setTargetPrice] = useState('')
  const [isAdding, setIsAdding] = useState(false)

  // Autocomplete state
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  const searchRef = useRef<HTMLDivElement>(null)

  // Fetch items on mount
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

  useEffect(() => {
    fetchItems()
  }, [fetchItems])

  // Autocomplete search with debounce
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

  // Click-outside handler for dropdown
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [])

  // Handle search result selection
  const handleResultClick = (result: SearchResult) => {
    setSymbol(result.symbol)
    setExchange(result.exchange)
    setSelectedSymbol(result.symbol)
    setShowDropdown(false)
  }

  // Handle input change (clear selectedSymbol when user types)
  const handleSymbolChange = (value: string) => {
    setSymbol(value)
    if (value !== selectedSymbol) {
      setSelectedSymbol(null)
    }
  }

  // Add item handler
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
      // Clear form
      setSymbol('')
      setExchange('NSE')
      setNotes('')
      setTargetPrice('')
      setSelectedSymbol(null)
      setSearchResults([])
      // Refresh
      await fetchItems()
      showToast.success('Symbol added to wishlist', 'actionCenter')
    } catch (_error) {
      showToast.error('Failed to add symbol', 'actionCenter')
    } finally {
      setIsAdding(false)
    }
  }

  // Remove item handler
  const handleRemove = async (itemId: number) => {
    try {
      await wishlistApi.removeItem(itemId)
      await fetchItems()
      showToast.success('Symbol removed from wishlist', 'actionCenter')
    } catch (_error) {
      showToast.error('Failed to remove symbol', 'actionCenter')
    }
  }

  // Refresh handler
  const handleRefresh = async () => {
    setIsLoading(true)
    await fetchItems()
    showToast.success('Wishlist refreshed', 'actionCenter')
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  return (
    <div className="py-6 space-y-6">
      {/* Header */}
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

      {/* Add Symbol Card */}
      <Card>
        <CardHeader>
          <CardTitle>Add Symbol</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {/* Symbol Search with Autocomplete */}
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

              {/* Autocomplete Dropdown */}
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

            {/* Exchange Selector */}
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

            {/* Notes Input */}
            <Input
              placeholder="Notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />

            {/* Target Price Input */}
            <Input
              type="number"
              step="any"
              placeholder="Target price (optional)"
              value={targetPrice}
              onChange={(e) => setTargetPrice(e.target.value)}
            />

            {/* Add Button */}
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

      {/* Wishlist Table */}
      <Card>
        <CardHeader>
          <CardTitle>Saved Symbols</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead>Target Price</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead className="w-[60px]">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                      No symbols in your wishlist yet
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell className="font-medium">{item.symbol}</TableCell>
                      <TableCell>
                        <span className="bg-primary text-primary-foreground px-2 py-0.5 rounded text-xs font-semibold">
                          {item.exchange}
                        </span>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {item.notes || '\u2014'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
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
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

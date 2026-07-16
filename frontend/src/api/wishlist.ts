import { webClient } from './client'

export interface WishlistItem {
  id: number
  symbol: string
  exchange: string
  notes: string | null
  target_price: number | null
  created_at: string | null
}

export interface WishlistAlert {
  id: number
  wishlist_item_id: number
  user_id: string
  symbol: string
  exchange: string
  condition_type: 'target_above' | 'target_below' | 'percent_up' | 'percent_down'
  condition_value: number
  reference_price: number | null
  is_active: boolean
  triggered: boolean
  triggered_at: string | null
  last_checked_price: number | null
  last_checked_at: string | null
  created_at: string | null
}

export interface CreateAlertRequest {
  wishlist_item_id: number
  condition_type: string
  condition_value: number
}

export interface AddWishlistItemRequest {
  symbol: string
  exchange: string
  notes?: string
  target_price?: number
}

export const wishlistApi = {
  getItems: async (): Promise<WishlistItem[]> => {
    const response = await webClient.get<WishlistItem[]>('/wishlist/api/items')
    return response.data
  },

  addItem: async (data: AddWishlistItemRequest): Promise<{ status: string; id: number }> => {
    const response = await webClient.post<{ status: string; id: number }>('/wishlist/api/items', data)
    return response.data
  },

  removeItem: async (itemId: number): Promise<void> => {
    await webClient.delete(`/wishlist/api/items/${itemId}`)
  },

  getAlerts: async (): Promise<WishlistAlert[]> => {
    const response = await webClient.get<WishlistAlert[]>('/wishlist/api/alerts')
    return response.data
  },

  getAlertsForItem: async (itemId: number): Promise<WishlistAlert[]> => {
    const response = await webClient.get<WishlistAlert[]>(`/wishlist/api/alerts/wishlist/${itemId}`)
    return response.data
  },

  createAlert: async (data: CreateAlertRequest): Promise<{ status: string; alert: WishlistAlert }> => {
    const response = await webClient.post('/wishlist/api/alerts', data)
    return response.data
  },

  toggleAlert: async (alertId: number): Promise<{ status: string; is_active: boolean }> => {
    const response = await webClient.patch(`/wishlist/api/alerts/${alertId}/toggle`)
    return response.data
  },

  deleteAlert: async (alertId: number): Promise<void> => {
    await webClient.delete(`/wishlist/api/alerts/${alertId}`)
  },

  deleteAlertsForItem: async (itemId: number): Promise<void> => {
    await webClient.delete(`/wishlist/api/alerts/wishlist/${itemId}`)
  },
}

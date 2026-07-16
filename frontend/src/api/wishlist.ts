import { webClient } from './client'

export interface WishlistItem {
  id: number
  symbol: string
  exchange: string
  notes: string | null
  target_price: number | null
  created_at: string | null
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
}

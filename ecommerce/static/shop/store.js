/* Pinia stores — global state for user, cart, toast notifications.
 *
 * Loaded via IIFE so we don't depend on ES module imports (CDN Vue 3 global
 * build doesn't support <script type="module"> cleanly with Pinia IIFE).
 */
(function (global) {
  const { defineStore } = Pinia;

  const useUserStore = defineStore('user', {
    state: () => ({
      userId: null,
      nickname: 'Guest',
      avatar: null,
      addresses: [],
      loaded: false,
    }),
    actions: {
      async init() {
        this.userId = global.ShopAPI.getUserId();
        try {
          const me = await global.ShopAPI.getMe();
          this.nickname = me.nickname;
          this.avatar = me.avatar;
        } catch (e) {
          console.warn('getMe failed:', e.message);
        }
        this.loaded = true;
      },
      async loadAddresses() {
        try {
          this.addresses = await global.ShopAPI.listAddresses();
        } catch (e) {
          console.warn('loadAddresses failed:', e.message);
          this.addresses = [];
        }
      },
    },
  });

  const useCartStore = defineStore('cart', {
    state: () => ({
      items: [],
      selectedCount: 0,
      selectedQuantity: 0,
      selectedSubtotal: 0,
      totalQuantity: 0,
      loaded: false,
      loading: false,
    }),
    getters: {
      count: (state) => state.totalQuantity,
    },
    actions: {
      async load() {
        if (this.loading) return;
        this.loading = true;
        try {
          const data = await global.ShopAPI.getCart();
          this.items = data.items || [];
          this.selectedCount = data.selected_count || 0;
          this.selectedQuantity = data.selected_quantity || 0;
          this.selectedSubtotal = data.selected_subtotal || 0;
          this.totalQuantity = data.total_quantity || 0;
          this.loaded = true;
        } catch (e) {
          console.warn('Cart load failed:', e.message);
        } finally {
          this.loading = false;
        }
      },
      async add(skuId, qty = 1) {
        await global.ShopAPI.addCartItem(skuId, qty);
        await this.load();
      },
      async update(itemId, updates) {
        await global.ShopAPI.updateCartItem(itemId, updates);
        await this.load();
      },
      async remove(itemId) {
        await global.ShopAPI.removeCartItem(itemId);
        await this.load();
      },
      async clear() {
        await global.ShopAPI.clearCart();
        await this.load();
      },
    },
  });

  const useToastStore = defineStore('toast', {
    state: () => ({ items: [] }),
    actions: {
      show(message, type = 'info', duration = 3000) {
        const id = Date.now() + Math.random();
        this.items.push({ id, message, type });
        setTimeout(() => {
          this.items = this.items.filter((t) => t.id !== id);
        }, duration);
      },
      success(msg, d) { this.show(msg, 'success', d); },
      error(msg, d) { this.show(msg, 'error', d || 4000); },
      warning(msg, d) { this.show(msg, 'warning', d); },
      info(msg, d) { this.show(msg, 'info', d); },
    },
  });

  const useCategoryStore = defineStore('category', {
    state: () => ({ tree: [], loaded: false }),
    actions: {
      async load() {
        if (this.loaded) return;
        try {
          this.tree = await global.ShopAPI.getCategories();
          this.loaded = true;
        } catch (e) {
          console.warn('Category load failed:', e.message);
        }
      },
    },
  });

  global.ShopStores = { useUserStore, useCartStore, useToastStore, useCategoryStore };
})(window);

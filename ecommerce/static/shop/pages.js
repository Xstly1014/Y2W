/* Page-level Vue components — one per route. Registered globally.
 *
 * Loaded AFTER components.js. Each page uses global.ShopAPI for data and
 * global.ShopStores for shared state.
 */
(function (global) {
  const { ref, reactive, computed, onMounted, watch } = Vue;
  const { useRoute, useRouter } = VueRouter;

  function fmtPrice(p) {
    const n = Number(p);
    if (isNaN(n)) return '0.00';
    return n.toFixed(2);
  }
  function fmtSales(n) {
    n = Number(n);
    if (n >= 10000) return (n / 10000).toFixed(1) + '万';
    return String(n);
  }
  function fmtDate(s) {
    if (!s) return '-';
    const d = new Date(s);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
           String(d.getDate()).padStart(2, '0') + ' ' +
           String(d.getHours()).padStart(2, '0') + ':' +
           String(d.getMinutes()).padStart(2, '0');
  }
  const STATUS_TEXT = {
    pending_payment: '待付款',
    paid: '已付款',
    shipped: '已发货',
    delivered: '已送达',
    completed: '已完成',
    cancelled: '已取消',
    refunded: '已退款',
  };

  // =========================================================================
  // HomePage
  // =========================================================================
  global.HomePage = {
    name: 'HomePage',
    components: { ProductCard: global.ProductCard },
    setup() {
      const catStore = global.ShopStores.useCategoryStore();
      const router = useRouter();
      const hot = ref([]);
      const newArrivals = ref([]);
      const loading = ref(true);

      onMounted(async () => {
        try {
          const [h, n] = await Promise.all([
            global.ShopAPI.getHotProducts(10),
            global.ShopAPI.getNewProducts(10),
          ]);
          hot.value = h;
          newArrivals.value = n;
        } catch (e) {
          console.error('HomePage load failed:', e);
        } finally {
          loading.value = false;
        }
      });

      function goProduct(p) { router.push('/product/' + p.id); }
      function goCategory(cat) { router.push({ path: '/category/' + cat.id, query: { name: cat.name } }); }
      function goHotAll() { router.push({ path: '/search', query: { sort_by: 'sales' } }); }
      function goNewAll() { router.push({ path: '/search', query: { sort_by: 'newest' } }); }

      return { catStore, hot, newArrivals, loading, goProduct, goCategory, goHotAll, goNewAll, fmtPrice };
    },
    template: `
      <div>
        <div class="container" style="padding-top: 16px;">
          <div class="banner">
            <div class="banner-side-cat">
              <div v-for="cat in catStore.tree" :key="cat.id"
                   class="banner-side-cat-item" @click="goCategory(cat)">
                <span>{{ cat.name }}</span>
                <span style="color: var(--color-text-muted);">›</span>
              </div>
            </div>
            <div class="banner-main">
              <div>
                <h1>优淘商城 · 限时大促</h1>
                <p>全场满 99 元免邮 · 7 天无理由退换 · 假一赔十</p>
              </div>
            </div>
            <div class="banner-side-promo">
              <div class="banner-side-promo-card">
                <h3 style="color: var(--color-primary);">限时秒杀</h3>
                <p>每日 10 点开抢</p>
              </div>
              <div class="banner-side-promo-card">
                <h3 style="color: var(--color-accent);">新人专享</h3>
                <p>首单立减 10 元</p>
              </div>
            </div>
          </div>
        </div>

        <div class="container">
          <div class="page-section">
            <div class="section-title">
              <h2>热销榜单</h2>
              <a @click="goHotAll" style="cursor: pointer;">查看更多 ›</a>
            </div>
            <div v-if="loading" class="product-grid">
              <div v-for="i in 10" :key="i" class="product-card">
                <div class="skeleton" style="aspect-ratio: 1;"></div>
                <div style="padding: 12px;">
                  <div class="skeleton" style="height: 36px;"></div>
                  <div class="skeleton" style="height: 22px; width: 60%; margin-top: 8px;"></div>
                </div>
              </div>
            </div>
            <div v-else class="product-grid">
              <product-card v-for="p in hot" :key="p.id" :product="p" @click="goProduct" />
            </div>
          </div>

          <div class="page-section">
            <div class="section-title">
              <h2>新品上架</h2>
              <a @click="goNewAll" style="cursor: pointer;">查看更多 ›</a>
            </div>
            <div class="product-grid">
              <product-card v-for="p in newArrivals" :key="p.id" :product="p" @click="goProduct" />
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // ProductListPage (also handles /search and /category/:id)
  // =========================================================================
  global.ProductListPage = {
    name: 'ProductListPage',
    components: { ProductCard: global.ProductCard, Pagination: global.Pagination, EmptyState: global.EmptyState },
    setup() {
      const route = useRoute();
      const router = useRouter();
      const toast = global.ShopStores.useToastStore();
      const catStore = global.ShopStores.useCategoryStore();
      const products = ref([]);
      const total = ref(0);
      const loading = ref(true);
      const filters = reactive({
        keyword: route.query.keyword || '',
        category_id: route.path.startsWith('/category/') ? Number(route.params.id) : null,
        brand: route.query.brand || '',
        price_min: '',
        price_max: '',
        sort_by: route.query.sort_by || 'default',
        page: Number(route.query.page) || 1,
        page_size: 20,
      });

      const pageTitle = computed(() => {
        if (filters.keyword) return `搜索"${filters.keyword}"`;
        if (filters.category_id) {
          const cat = findCat(catStore.tree, filters.category_id);
          return cat ? cat.name : '商品分类';
        }
        return '全部商品';
      });

      function findCat(tree, id) {
        for (const c of tree) {
          if (c.id === id) return c;
          if (c.children) {
            const f = findCat(c.children, id);
            if (f) return f;
          }
        }
        return null;
      }

      async function load() {
        loading.value = true;
        try {
          const params = {
            page: filters.page,
            page_size: filters.page_size,
            sort_by: filters.sort_by,
          };
          if (filters.keyword) params.keyword = filters.keyword;
          if (filters.category_id) params.category_id = filters.category_id;
          if (filters.brand) params.brand = filters.brand;
          if (filters.price_min) params.price_min = filters.price_min;
          if (filters.price_max) params.price_max = filters.price_max;
          const resp = await global.ShopAPI.listProducts(params);
          products.value = resp.items || [];
          total.value = resp.total || 0;
        } catch (e) {
          toast.error('加载商品失败: ' + e.message);
        } finally {
          loading.value = false;
        }
      }

      function changePage(p) {
        filters.page = p;
        scrolltotop();
        load();
      }
      function changeSort(s) {
        filters.sort_by = s;
        filters.page = 1;
        load();
      }
      function applyPriceFilter() {
        filters.page = 1;
        load();
      }
      function goProduct(p) { router.push('/product/' + p.id); }
      function scrolltotop() { window.scrollTo({ top: 0, behavior: 'smooth' }); }

      onMounted(() => {
        catStore.load();
        load();
      });

      watch(() => [route.query.keyword, route.params.id, route.query.sort_by], () => {
        filters.keyword = route.query.keyword || '';
        filters.category_id = route.path.startsWith('/category/') ? Number(route.params.id) : null;
        filters.sort_by = route.query.sort_by || 'default';
        filters.page = 1;
        load();
      });

      return {
        catStore, products, total, loading, filters, pageTitle,
        changePage, changeSort, applyPriceFilter, goProduct, fmtPrice,
      };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title">
            <h2>{{ pageTitle }}</h2>
            <span style="font-size: 13px; color: var(--color-text-tertiary);">共 {{ total }} 件商品</span>
          </div>

          <div class="filter-bar">
            <div class="filter-bar-group">
              <span class="filter-bar-label">排序：</span>
              <button :class="['filter-bar-btn', filters.sort_by === 'default' ? 'active' : '']" @click="changeSort('default')">综合</button>
              <button :class="['filter-bar-btn', filters.sort_by === 'sales' ? 'active' : '']" @click="changeSort('sales')">销量</button>
              <button :class="['filter-bar-btn', filters.sort_by === 'price_asc' ? 'active' : '']" @click="changeSort('price_asc')">价格↑</button>
              <button :class="['filter-bar-btn', filters.sort_by === 'price_desc' ? 'active' : '']" @click="changeSort('price_desc')">价格↓</button>
              <button :class="['filter-bar-btn', filters.sort_by === 'rating' ? 'active' : '']" @click="changeSort('rating')">评分</button>
              <button :class="['filter-bar-btn', filters.sort_by === 'newest' ? 'active' : '']" @click="changeSort('newest')">最新</button>
            </div>
            <div class="filter-bar-group">
              <span class="filter-bar-label">价格：</span>
              <input v-model="filters.price_min" type="number" placeholder="最低" style="width: 70px; padding: 4px 8px; border: 1px solid var(--color-border); border-radius: 4px;" />
              <span>-</span>
              <input v-model="filters.price_max" type="number" placeholder="最高" style="width: 70px; padding: 4px 8px; border: 1px solid var(--color-border); border-radius: 4px;" />
              <button class="filter-bar-btn" @click="applyPriceFilter">确定</button>
            </div>
          </div>

          <div v-if="loading" class="product-grid">
            <div v-for="i in 10" :key="i" class="product-card">
              <div class="skeleton" style="aspect-ratio: 1;"></div>
              <div style="padding: 12px;">
                <div class="skeleton" style="height: 36px;"></div>
                <div class="skeleton" style="height: 22px; width: 60%; margin-top: 8px;"></div>
              </div>
            </div>
          </div>
          <div v-else-if="products.length === 0">
            <empty-state icon="box" title="没有找到符合条件的商品" desc="试试调整筛选条件或搜索其他关键词" />
          </div>
          <div v-else class="product-grid">
            <product-card v-for="p in products" :key="p.id" :product="p" @click="goProduct" />
          </div>

          <pagination
            v-if="total > filters.page_size"
            :page="filters.page"
            :page-size="filters.page_size"
            :total="total"
            @change="changePage"
          />
        </div>
      </div>
    `,
  };

  // =========================================================================
  // ProductDetailPage
  // =========================================================================
  global.ProductDetailPage = {
    name: 'ProductDetailPage',
    components: { ProductCard: global.ProductCard, EmptyState: global.EmptyState },
    setup() {
      const route = useRoute();
      const router = useRouter();
      const toast = global.ShopStores.useToastStore();
      const cartStore = global.ShopStores.useCartStore();
      const product = ref(null);
      const related = ref([]);
      const loading = ref(true);
      const activeImage = ref('');
      const selectedSku = ref(null);
      const quantity = ref(1);

      async function load() {
        loading.value = true;
        const id = route.params.id;
        try {
          product.value = await global.ShopAPI.getProduct(id);
          if (product.value && product.value.images && product.value.images.length) {
            activeImage.value = product.value.images[0].url;
          } else if (product.value) {
            activeImage.value = product.value.main_image;
          }
          // Pre-select first active SKU.
          if (product.value && product.value.skus) {
            const first = product.value.skus.find(s => s.is_active) || product.value.skus[0];
            selectedSku.value = first || null;
          }
          // Load related.
          try {
            related.value = await global.ShopAPI.getRelated(id, 6);
          } catch (e) { related.value = []; }
        } catch (e) {
          toast.error('商品加载失败: ' + e.message);
          product.value = null;
        } finally {
          loading.value = false;
        }
      }

      function selectSku(sku) {
        if (!sku.is_active) return;
        selectedSku.value = sku;
        if (quantity.value > sku.stock - sku.reserved) {
          quantity.value = Math.max(1, sku.stock - sku.reserved);
        }
      }
      function selectImage(url) { activeImage.value = url; }
      function incQty() {
        if (!selectedSku.value) return;
        const avail = selectedSku.value.stock - selectedSku.value.reserved;
        if (quantity.value < Math.min(99, avail)) quantity.value++;
      }
      function decQty() {
        if (quantity.value > 1) quantity.value--;
      }

      async function addToCart() {
        if (!selectedSku.value) { toast.warning('请选择商品规格'); return; }
        try {
          await cartStore.add(selectedSku.value.id, quantity.value);
          toast.success('已加入购物车');
        } catch (e) {
          toast.error('加购失败: ' + e.message);
        }
      }
      async function buyNow() {
        if (!selectedSku.value) { toast.warning('请选择商品规格'); return; }
        try {
          await cartStore.add(selectedSku.value.id, quantity.value, false);
          router.push('/checkout?from=buynow&sku=' + selectedSku.value.id);
        } catch (e) {
          toast.error('下单失败: ' + e.message);
        }
      }
      function goProduct(p) { router.push('/product/' + p.id); }

      onMounted(load);
      watch(() => route.params.id, load);

      return {
        product, related, loading, activeImage, selectedSku, quantity,
        selectSku, selectImage, incQty, decQty, addToCart, buyNow, goProduct, fmtPrice, fmtSales,
      };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div v-if="loading" class="product-detail">
          <div>
            <div class="skeleton" style="width: 480px; height: 480px;"></div>
            <div style="display: flex; gap: 8px; margin-top: 12px;">
              <div v-for="i in 4" :key="i" class="skeleton" style="width: 60px; height: 60px;"></div>
            </div>
          </div>
          <div>
            <div class="skeleton" style="height: 28px; width: 80%;"></div>
            <div class="skeleton" style="height: 16px; width: 60%; margin-top: 12px;"></div>
            <div class="skeleton" style="height: 80px; margin-top: 24px;"></div>
          </div>
        </div>
        <div v-else-if="!product" class="product-detail">
          <empty-state icon="box" title="商品不存在或已下架" />
        </div>
        <div v-else>
          <div class="product-detail">
            <div class="product-detail-images">
              <div class="main-image">
                <img :src="activeImage" :alt="product.title" />
              </div>
              <div class="product-detail-thumbs" v-if="product.images && product.images.length">
                <div v-for="img in product.images" :key="img.id"
                     :class="['product-detail-thumb', img.url === activeImage ? 'active' : '']"
                     @click="selectImage(img.url)">
                  <img :src="img.url" :alt="product.title" />
                </div>
              </div>
            </div>
            <div class="product-detail-info">
              <h1>{{ product.title }}</h1>
              <div v-if="product.subtitle" class="product-detail-subtitle">{{ product.subtitle }}</div>
              <div class="product-detail-price-row">
                <span class="product-detail-price-current">
                  <span style="font-size: 18px;">¥</span>{{ fmtPrice(selectedSku ? selectedSku.price : product.price_min) }}
                </span>
                <span v-if="product.original_price && Number(product.original_price) > Number(product.price_min)" class="product-detail-price-original">
                  ¥{{ fmtPrice(product.original_price) }}
                </span>
              </div>
              <div class="product-detail-meta">
                <div class="product-detail-meta-item">
                  <div class="product-detail-meta-label">销量</div>
                  <div class="product-detail-meta-value">{{ fmtSales(product.sales_count) }}</div>
                </div>
                <div class="product-detail-meta-item">
                  <div class="product-detail-meta-label">评分</div>
                  <div class="product-detail-meta-value" style="color: var(--color-warning);">★ {{ Number(product.rating_avg).toFixed(1) }}</div>
                </div>
                <div class="product-detail-meta-item">
                  <div class="product-detail-meta-label">评价数</div>
                  <div class="product-detail-meta-value">{{ product.rating_count }}</div>
                </div>
              </div>
              <div v-if="product.skus && product.skus.length > 1" class="product-detail-sku">
                <div style="font-size: 13px; color: var(--color-text-tertiary);">选择规格：</div>
                <div class="product-detail-sku-row">
                  <button v-for="sku in product.skus" :key="sku.id"
                          :class="['product-detail-sku-btn',
                                   selectedSku && selectedSku.id === sku.id ? 'active' : '',
                                   !sku.is_active || (sku.stock - sku.reserved) <= 0 ? 'disabled' : '']"
                          :disabled="!sku.is_active || (sku.stock - sku.reserved) <= 0"
                          @click="selectSku(sku)">
                    {{ sku.spec || '默认' }}
                    <span v-if="!sku.is_active || (sku.stock - sku.reserved) <= 0" style="margin-left: 8px; font-size: 11px;">(无货)</span>
                  </button>
                </div>
              </div>
              <div class="product-detail-qty">
                <span style="font-size: 13px; color: var(--color-text-tertiary);">数量：</span>
                <div class="product-detail-qty-controls">
                  <button @click="decQty" :disabled="quantity <= 1">-</button>
                  <input v-model.number="quantity" type="number" min="1" max="99" />
                  <button @click="incQty">+</button>
                </div>
                <span v-if="selectedSku" style="font-size: 12px; color: var(--color-text-tertiary);">
                  库存 {{ selectedSku.stock - selectedSku.reserved }} 件
                </span>
              </div>
              <div class="product-detail-actions">
                <button class="btn-secondary btn-large" @click="addToCart" style="flex: 1;">加入购物车</button>
                <button class="btn-primary btn-large" @click="buyNow" style="flex: 1;">立即购买</button>
              </div>
              <div v-if="product.tags" style="margin-top: 16px; font-size: 12px; color: var(--color-text-tertiary);">
                标签：{{ product.tags }}
              </div>
            </div>
          </div>

          <div style="margin-top: 24px;">
            <div class="section-title"><h2>📝 商品详情</h2></div>
            <div style="background: var(--color-surface); border-radius: 8px; padding: 24px; line-height: 1.8; font-size: 14px;">
              {{ product.description }}
            </div>
          </div>

          <div v-if="related.length > 0" style="margin-top: 24px;">
            <div class="section-title"><h2>💡 看了又看</h2></div>
            <div class="product-grid">
              <product-card v-for="p in related" :key="p.id" :product="p" @click="goProduct" />
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // CartPage
  // =========================================================================
  global.CartPage = {
    name: 'CartPage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const cartStore = global.ShopStores.useCartStore();
      const toast = global.ShopStores.useToastStore();
      const router = useRouter();
      const loading = ref(true);

      onMounted(async () => {
        await cartStore.load();
        loading.value = false;
      });

      const allSelected = computed(() =>
        cartStore.items.length > 0 && cartStore.items.every(i => i.selected)
      );

      async function toggleSelect(item) {
        try {
          await cartStore.update(item.id, { selected: !item.selected });
        } catch (e) { toast.error(e.message); }
      }
      async function toggleSelectAll() {
        const target = !allSelected.value;
        try {
          for (const item of cartStore.items) {
            if (item.selected !== target) {
              await global.ShopAPI.updateCartItem(item.id, { selected: target });
            }
          }
          await cartStore.load();
        } catch (e) { toast.error(e.message); }
      }
      async function changeQty(item, delta) {
        const newQty = item.quantity + delta;
        if (newQty < 1) return;
        if (newQty > (item.available_stock || 99)) {
          toast.warning('库存不足');
          return;
        }
        try {
          await cartStore.update(item.id, { quantity: newQty });
        } catch (e) { toast.error(e.message); }
      }
      async function removeItem(item) {
        if (!confirm('确定要删除该商品吗？')) return;
        try {
          await cartStore.remove(item.id);
          toast.success('已删除');
        } catch (e) { toast.error(e.message); }
      }
      async function clearAll() {
        if (!confirm('确定要清空购物车吗？')) return;
        try {
          await cartStore.clear();
          toast.success('购物车已清空');
        } catch (e) { toast.error(e.message); }
      }
      function checkout() {
        if (cartStore.selectedCount === 0) {
          toast.warning('请先选择商品');
          return;
        }
        router.push('/checkout');
      }
      function goHome() { router.push('/'); }

      return { cartStore, loading, allSelected, toggleSelect, toggleSelectAll,
               changeQty, removeItem, clearAll, checkout, goHome, fmtPrice };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>我的购物车</h2></div>
          <div v-if="loading" class="cart-page">
            <div v-for="i in 3" :key="i" class="skeleton" style="height: 100px; margin-bottom: 12px;"></div>
          </div>
          <div v-else-if="cartStore.items.length === 0" class="cart-page">
            <empty-state icon="cart" title="购物车空空如也" desc="快去挑选心仪的商品吧">
              <button class="btn-primary" @click="goHome">去逛逛</button>
            </empty-state>
          </div>
          <div v-else class="cart-page">
            <table class="cart-table">
              <thead>
                <tr>
                  <th style="width: 40px;">
                    <input type="checkbox" :checked="allSelected" @change="toggleSelectAll" />
                  </th>
                  <th>商品信息</th>
                  <th style="width: 100px; text-align: center;">单价</th>
                  <th style="width: 140px; text-align: center;">数量</th>
                  <th style="width: 100px; text-align: center;">小计</th>
                  <th style="width: 80px; text-align: center;">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in cartStore.items" :key="item.id">
                  <td style="text-align: center;">
                    <input type="checkbox" :checked="item.selected" @change="toggleSelect(item)" />
                  </td>
                  <td>
                    <div class="cart-item-cell">
                      <div class="cart-item-thumb">
                        <img :src="item.product_image" :alt="item.product_title" />
                      </div>
                      <div>
                        <div class="cart-item-title">{{ item.product_title }}</div>
                        <div v-if="item.sku_spec" class="cart-item-spec">{{ item.sku_spec }}</div>
                      </div>
                    </div>
                  </td>
                  <td style="text-align: center; color: var(--color-primary);">¥{{ fmtPrice(item.sku_price) }}</td>
                  <td style="text-align: center;">
                    <div class="cart-qty-controls" style="margin: 0 auto;">
                      <button @click="changeQty(item, -1)" :disabled="item.quantity <= 1">-</button>
                      <input :value="item.quantity" readonly />
                      <button @click="changeQty(item, 1)" :disabled="item.quantity >= (item.available_stock || 99)">+</button>
                    </div>
                  </td>
                  <td style="text-align: center; color: var(--color-primary); font-weight: 600;">
                    ¥{{ fmtPrice(Number(item.sku_price) * item.quantity) }}
                  </td>
                  <td style="text-align: center;">
                    <button class="btn-ghost" style="padding: 4px 10px; font-size: 12px;" @click="removeItem(item)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="cart-summary-bar">
              <div class="cart-summary-bar-left">
                <input type="checkbox" :checked="allSelected" @change="toggleSelectAll" />
                <span>全选</span>
                <button class="btn-ghost" style="padding: 4px 10px; font-size: 12px;" @click="clearAll">清空</button>
              </div>
              <div class="cart-summary-bar-right">
                <span>已选 <strong style="color: var(--color-primary);">{{ cartStore.selectedCount }}</strong> 件 / {{ cartStore.selectedQuantity }} 个</span>
                <span>合计：<span class="cart-summary-total">¥{{ fmtPrice(cartStore.selectedSubtotal) }}</span></span>
                <button class="btn-primary btn-large" @click="checkout" :disabled="cartStore.selectedCount === 0">去结算</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // CheckoutPage
  // =========================================================================
  global.CheckoutPage = {
    name: 'CheckoutPage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const toast = global.ShopStores.useToastStore();
      const userStore = global.ShopStores.useUserStore();
      const cartStore = global.ShopStores.useCartStore();
      const router = useRouter();
      const route = useRoute();
      const addresses = ref([]);
      const selectedAddressId = ref(null);
      const remark = ref('');
      const couponCode = ref('');
      const placing = ref(false);

      onMounted(async () => {
        await Promise.all([userStore.loadAddresses(), cartStore.load()]);
        addresses.value = userStore.addresses;
        const def = addresses.value.find(a => a.is_default) || addresses.value[0];
        if (def) selectedAddressId.value = def.id;
      });

      async function placeOrder() {
        if (!selectedAddressId.value) {
          toast.warning('请选择收货地址');
          return;
        }
        placing.value = true;
        try {
          const order = await global.ShopAPI.createOrder({
            address_id: selectedAddressId.value,
            coupon_code: couponCode.value || null,
            remark: remark.value,
          });
          toast.success('订单创建成功');
          // Navigate to pay page.
          router.push('/pay/' + order.id);
        } catch (e) {
          toast.error('下单失败: ' + e.message);
        } finally {
          placing.value = false;
        }
      }
      function goAddress() { router.push('/user/addresses'); }

      return { cartStore, addresses, selectedAddressId, remark, couponCode,
               placing, placeOrder, goAddress, fmtPrice };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>确认订单</h2></div>
          <div class="cart-page">
            <div style="margin-bottom: 24px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 16px;">收货地址</h3>
                <button class="btn-ghost" style="font-size: 12px; padding: 4px 12px;" @click="goAddress">管理地址</button>
              </div>
              <div v-if="addresses.length === 0" style="padding: 16px; background: var(--color-surface-2); border-radius: 6px;">
                <span style="color: var(--color-text-tertiary);">还没有收货地址</span>
                <button class="btn-primary" style="margin-left: 12px; padding: 4px 12px; font-size: 12px;" @click="goAddress">添加地址</button>
              </div>
              <div v-else>
                <div v-for="addr in addresses" :key="addr.id"
                     :style="['padding: 12px 16px; border: 2px solid; border-radius: 6px; margin-bottom: 8px; cursor: pointer;',
                              selectedAddressId === addr.id ? 'border-color: var(--color-primary); background: var(--color-primary-light);' : 'border-color: var(--color-border);']"
                     @click="selectedAddressId = addr.id">
                  <div>
                    <strong>{{ addr.recipient }}</strong>
                    <span style="margin-left: 12px; color: var(--color-text-tertiary);">{{ addr.phone }}</span>
                    <span v-if="addr.is_default" style="margin-left: 8px; padding: 2px 6px; background: var(--color-primary); color: white; font-size: 11px; border-radius: 3px;">默认</span>
                  </div>
                  <div style="margin-top: 4px; font-size: 13px; color: var(--color-text-secondary);">
                    {{ addr.province }}{{ addr.city }}{{ addr.district }}{{ addr.detail }}
                  </div>
                </div>
              </div>
            </div>

            <div style="margin-bottom: 24px;">
              <h3 style="font-size: 16px; margin-bottom: 12px;">商品清单</h3>
              <table class="cart-table">
                <thead>
                  <tr>
                    <th>商品</th>
                    <th style="width: 100px; text-align: center;">单价</th>
                    <th style="width: 80px; text-align: center;">数量</th>
                    <th style="width: 100px; text-align: center;">小计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in cartStore.items.filter(i => i.selected)" :key="item.id">
                    <td>
                      <div class="cart-item-cell">
                        <div class="cart-item-thumb"><img :src="item.product_image" /></div>
                        <div>
                          <div class="cart-item-title">{{ item.product_title }}</div>
                          <div v-if="item.sku_spec" class="cart-item-spec">{{ item.sku_spec }}</div>
                        </div>
                      </div>
                    </td>
                    <td style="text-align: center;">¥{{ fmtPrice(item.sku_price) }}</td>
                    <td style="text-align: center;">{{ item.quantity }}</td>
                    <td style="text-align: center; color: var(--color-primary); font-weight: 600;">¥{{ fmtPrice(Number(item.sku_price) * item.quantity) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div style="margin-bottom: 24px;">
              <h3 style="font-size: 16px; margin-bottom: 12px;">订单备注</h3>
              <input v-model="remark" class="form-input" placeholder="选填，给卖家留言（256字以内）" maxlength="256" />
            </div>

            <div style="margin-bottom: 24px;">
              <h3 style="font-size: 16px; margin-bottom: 12px;">🎫 优惠券</h3>
              <input v-model="couponCode" class="form-input" style="max-width: 240px;" placeholder="输入优惠券码（如 NEW10）" />
              <div style="margin-top: 8px; font-size: 12px; color: var(--color-text-tertiary);">
                可用：NEW10（满 50 减 10）· SAVE20（满 100 减 20）· PCT15（满 200 打 85 折）
              </div>
            </div>

            <div style="text-align: right; padding-top: 16px; border-top: 1px solid var(--color-divider);">
              <div style="font-size: 14px; margin-bottom: 8px;">
                共 <strong style="color: var(--color-primary);">{{ cartStore.selectedQuantity }}</strong> 件商品，
                合计：<span style="font-size: 24px; font-weight: 700; color: var(--color-primary);">¥{{ fmtPrice(cartStore.selectedSubtotal) }}</span>
              </div>
              <button class="btn-primary btn-large" @click="placeOrder" :disabled="placing || cartStore.selectedCount === 0">
                {{ placing ? '提交中...' : '提交订单' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // PayPage
  // =========================================================================
  global.PayPage = {
    name: 'PayPage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const route = useRoute();
      const router = useRouter();
      const toast = global.ShopStores.useToastStore();
      const order = ref(null);
      const method = ref('alipay');
      const paying = ref(false);
      const loading = ref(true);

      onMounted(async () => {
        try {
          order.value = await global.ShopAPI.getOrder(route.params.id);
        } catch (e) {
          toast.error('订单加载失败: ' + e.message);
        } finally {
          loading.value = false;
        }
      });

      async function pay() {
        if (!order.value) return;
        paying.value = true;
        try {
          const result = await global.ShopAPI.payOrder(order.value.id, method.value);
          if (result.success) {
            toast.success('支付成功');
            router.push('/pay-success/' + order.value.id);
          } else {
            toast.error('支付失败: ' + result.message);
          }
        } catch (e) {
          toast.error('支付失败: ' + e.message);
        } finally {
          paying.value = false;
        }
      }
      function cancel() {
        if (confirm('确定取消支付吗？')) {
          router.push('/orders');
        }
      }

      // Payment methods — `initial` is rendered inside a CSS circle (see
      // `.pay-method-icon` in styles.css) instead of an emoji, keeping
      // the visual language consistent with the rest of the premium UI.
      const METHODS = [
        { id: 'alipay', name: '支付宝', initial: '支', color: '#1677ff' },
        { id: 'wechat', name: '微信支付', initial: '微', color: '#07c160' },
        { id: 'card', name: '银行卡', initial: '卡', color: '#ff3b30' },
        { id: 'balance', name: '余额支付', initial: '余', color: '#ff9500' },
      ];

      return { order, method, paying, loading, pay, cancel, METHODS, fmtPrice };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>支付订单</h2></div>
          <div v-if="loading" class="cart-page">
            <div class="skeleton" style="height: 200px;"></div>
          </div>
          <div v-else-if="!order" class="cart-page">
            <empty-state icon="box" title="订单不存在" />
          </div>
          <div v-else class="cart-page">
            <div style="text-align: center; padding: 24px 0; border-bottom: 1px solid var(--color-divider);">
              <div style="font-size: 14px; color: var(--color-text-tertiary);">订单号</div>
              <div style="font-family: monospace; font-size: 16px; margin: 8px 0;">{{ order.order_no }}</div>
              <div style="font-size: 32px; color: var(--color-primary); font-weight: 700; margin-top: 16px;">
                ¥{{ fmtPrice(order.total_amount) }}
              </div>
            </div>
            <div style="padding: 24px 0;">
              <h3 style="font-size: 16px; margin-bottom: 16px;">选择支付方式</h3>
              <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;">
                <div v-for="m in METHODS" :key="m.id"
                     :class="['pay-method', method === m.id ? 'pay-method-active' : '']"
                     :style="method === m.id ? { borderColor: m.color, background: m.color + '11' } : { borderColor: 'var(--color-border)' }"
                     @click="method = m.id">
                  <div class="pay-method-icon" :style="{ background: m.color }">{{ m.initial }}</div>
                  <div class="pay-method-name">{{ m.name }}</div>
                </div>
              </div>
            </div>
            <div style="text-align: center; padding: 16px 0;">
              <div class="pay-secure-notice">本支付为模拟支付，不会产生真实交易</div>
              <button class="btn-primary btn-large" @click="pay" :disabled="paying" style="width: 240px;">
                {{ paying ? '支付中...' : '确认支付 ¥' + (order ? fmtPrice(order.total_amount) : '') }}
              </button>
              <div style="margin-top: 12px;">
                <button class="btn-ghost" @click="cancel" style="font-size: 12px; padding: 4px 12px;">取消支付</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // PaySuccessPage
  // =========================================================================
  global.PaySuccessPage = {
    name: 'PaySuccessPage',
    setup() {
      const route = useRoute();
      const router = useRouter();
      const orderId = computed(() => route.params.id);
      function goOrders() { router.push('/orders'); }
      function goHome() { router.push('/'); }
      return { orderId, goOrders, goHome };
    },
    template: `
      <div class="container" style="padding-top: 32px;">
        <div class="cart-page" style="text-align: center; padding: 48px 24px;">
          <div class="pay-success-icon"></div>
          <h2 style="margin-top: 16px; font-size: 24px; color: var(--color-success);">支付成功</h2>
          <p style="margin-top: 8px; color: var(--color-text-tertiary);">订单号：{{ orderId }}</p>
          <p style="margin-top: 16px; font-size: 13px; color: var(--color-text-tertiary);">
            我们将尽快为您发货，预计 2-3 天送达
          </p>
          <div style="margin-top: 32px;">
            <button class="btn-primary" @click="goOrders">查看订单</button>
            <button class="btn-ghost" style="margin-left: 12px;" @click="goHome">继续购物</button>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // OrderListPage
  // =========================================================================
  global.OrderListPage = {
    name: 'OrderListPage',
    components: { EmptyState: global.EmptyState, Pagination: global.Pagination },
    setup() {
      const route = useRoute();
      const router = useRouter();
      const toast = global.ShopStores.useToastStore();
      const orders = ref([]);
      const total = ref(0);
      const loading = ref(true);
      const statusFilter = ref(route.query.status || '');
      const page = ref(1);
      const pageSize = ref(10);

      const TABS = [
        { id: '', name: '全部' },
        { id: 'pending_payment', name: '待付款' },
        { id: 'paid', name: '待发货' },
        { id: 'shipped', name: '待收货' },
        { id: 'completed', name: '已完成' },
        { id: 'cancelled', name: '已取消' },
      ];

      async function load() {
        loading.value = true;
        try {
          const params = { page: page.value, page_size: pageSize.value };
          if (statusFilter.value) params.status = statusFilter.value;
          const resp = await global.ShopAPI.listOrders(params);
          orders.value = resp.items || [];
          total.value = resp.total || 0;
        } catch (e) {
          toast.error('订单加载失败: ' + e.message);
        } finally {
          loading.value = false;
        }
      }
      function changeTab(s) {
        statusFilter.value = s;
        page.value = 1;
        load();
      }
      function changePage(p) {
        page.value = p;
        load();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
      function goDetail(o) { router.push('/order/' + o.id); }
      function goPay(o) { router.push('/pay/' + o.id); }
      async function cancel(o) {
        if (!confirm('确定取消该订单吗？')) return;
        try {
          await global.ShopAPI.cancelOrder(o.id);
          toast.success('订单已取消');
          await load();
        } catch (e) { toast.error(e.message); }
      }
      function statusText(s) { return STATUS_TEXT[s] || s; }
      function statusClass(s) { return s; }

      onMounted(load);

      return { orders, total, loading, statusFilter, page, pageSize, TABS,
               changeTab, changePage, goDetail, goPay, cancel, statusText,
               statusClass, fmtPrice, fmtDate };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>我的订单</h2></div>
          <div class="filter-bar">
            <button v-for="t in TABS" :key="t.id"
                    :class="['filter-bar-btn', statusFilter === t.id ? 'active' : '']"
                    @click="changeTab(t.id)">
              {{ t.name }}
            </button>
          </div>
          <div v-if="loading">
            <div v-for="i in 3" :key="i" class="skeleton" style="height: 160px; margin-bottom: 12px;"></div>
          </div>
          <div v-else-if="orders.length === 0">
            <empty-state icon="box" title="还没有订单" desc="快去购物吧">
              <button class="btn-primary" @click="$router.push('/')">去逛逛</button>
            </empty-state>
          </div>
          <div v-else>
            <div v-for="order in orders" :key="order.id" class="order-card">
              <div class="order-card-header">
                <div>
                  <span>订单号: {{ order.order_no }}</span>
                  <span style="margin-left: 16px;">下单时间: {{ fmtDate(order.created_at) }}</span>
                </div>
                <span :class="['order-card-status', statusClass(order.status)]">{{ statusText(order.status) }}</span>
              </div>
              <div class="order-card-items">
                <div v-for="item in order.items" :key="item.id" class="order-card-item">
                  <div class="order-card-item-thumb">
                    <img :src="item.product_image" :alt="item.product_title" />
                  </div>
                  <div class="order-card-item-info">
                    <div>{{ item.product_title }}</div>
                    <div class="order-card-item-spec">{{ item.sku_spec || '默认' }} × {{ item.quantity }}</div>
                  </div>
                  <div style="color: var(--color-primary);">¥{{ fmtPrice(item.subtotal) }}</div>
                </div>
              </div>
              <div class="order-card-footer">
                <span class="order-card-total">共 {{ order.items.length }} 件商品，合计 <span class="order-card-total-amount">¥{{ fmtPrice(order.total_amount) }}</span></span>
                <button class="btn-ghost" @click="goDetail(order)">详情</button>
                <button v-if="order.status === 'pending_payment'" class="btn-primary" @click="goPay(order)">去支付</button>
                <button v-if="order.status === 'pending_payment'" class="btn-ghost" @click="cancel(order)">取消订单</button>
              </div>
            </div>
            <pagination v-if="total > pageSize" :page="page" :page-size="pageSize" :total="total" @change="changePage" />
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // OrderDetailPage
  // =========================================================================
  global.OrderDetailPage = {
    name: 'OrderDetailPage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const route = useRoute();
      const router = useRouter();
      const toast = global.ShopStores.useToastStore();
      const order = ref(null);
      const loading = ref(true);

      async function load() {
        loading.value = true;
        try {
          order.value = await global.ShopAPI.getOrder(route.params.id);
        } catch (e) {
          toast.error('订单加载失败: ' + e.message);
        } finally {
          loading.value = false;
        }
      }
      async function cancel() {
        if (!confirm('确定取消该订单吗？')) return;
        try {
          await global.ShopAPI.cancelOrder(order.value.id);
          toast.success('订单已取消');
          await load();
        } catch (e) { toast.error(e.message); }
      }
      function goPay() { router.push('/pay/' + order.value.id); }
      function statusText(s) { return STATUS_TEXT[s] || s; }
      function statusClass(s) { return s; }

      onMounted(load);
      return { order, loading, cancel, goPay, statusText, statusClass, fmtPrice, fmtDate };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>订单详情</h2></div>
          <div v-if="loading" class="cart-page">
            <div class="skeleton" style="height: 200px;"></div>
          </div>
          <div v-else-if="!order" class="cart-page">
            <empty-state icon="box" title="订单不存在" />
          </div>
          <div v-else class="cart-page">
            <div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--color-divider);">
              <div>
                <div style="font-size: 14px; color: var(--color-text-tertiary);">订单号</div>
                <div style="font-family: monospace; font-size: 16px; margin-top: 4px;">{{ order.order_no }}</div>
              </div>
              <div style="text-align: right;">
                <div style="font-size: 14px; color: var(--color-text-tertiary);">订单状态</div>
                <span :class="['order-card-status', statusClass(order.status)]" style="display: inline-block; margin-top: 4px; font-size: 14px; padding: 4px 12px;">{{ statusText(order.status) }}</span>
              </div>
            </div>

            <div style="padding: 16px 0; border-bottom: 1px solid var(--color-divider);">
              <h3 style="font-size: 14px; margin-bottom: 12px;">收货信息</h3>
              <div style="font-size: 13px; line-height: 1.8;">
                <div><strong>{{ order.recipient }}</strong> · {{ order.phone }}</div>
                <div style="color: var(--color-text-secondary);">{{ order.address_line }}</div>
              </div>
            </div>

            <div style="padding: 16px 0; border-bottom: 1px solid var(--color-divider);">
              <h3 style="font-size: 14px; margin-bottom: 12px;">商品清单</h3>
              <div v-for="item in order.items" :key="item.id" class="order-card-item">
                <div class="order-card-item-thumb"><img :src="item.product_image" /></div>
                <div class="order-card-item-info">
                  <div>{{ item.product_title }}</div>
                  <div class="order-card-item-spec">{{ item.sku_spec || '默认' }} × {{ item.quantity }}</div>
                </div>
                <div style="text-align: right; color: var(--color-primary);">¥{{ fmtPrice(item.subtotal) }}</div>
              </div>
            </div>

            <div style="padding: 16px 0; border-bottom: 1px solid var(--color-divider); font-size: 13px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span>商品总额</span><span>¥{{ fmtPrice(order.items_subtotal) }}</span>
              </div>
              <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                <span>运费</span><span>¥{{ fmtPrice(order.shipping_fee) }}</span>
              </div>
              <div v-if="Number(order.discount_amount) > 0" style="display: flex; justify-content: space-between; margin-bottom: 8px; color: var(--color-primary);">
                <span>优惠</span><span>-¥{{ fmtPrice(order.discount_amount) }}</span>
              </div>
              <div style="display: flex; justify-content: space-between; font-size: 18px; font-weight: 700; color: var(--color-primary); margin-top: 12px;">
                <span>实付金额</span><span>¥{{ fmtPrice(order.total_amount) }}</span>
              </div>
            </div>

            <div style="padding: 16px 0; font-size: 13px; color: var(--color-text-tertiary);">
              <div>下单时间: {{ fmtDate(order.created_at) }}</div>
              <div v-if="order.paid_at">支付时间: {{ fmtDate(order.paid_at) }} ({{ order.payment_method }})</div>
              <div v-if="order.tracking_no">物流单号: {{ order.tracking_no }}</div>
              <div v-if="order.shipped_at">发货时间: {{ fmtDate(order.shipped_at) }}</div>
              <div v-if="order.delivered_at">送达时间: {{ fmtDate(order.delivered_at) }}</div>
              <div v-if="order.remark">备注: {{ order.remark }}</div>
            </div>

            <div style="text-align: right; padding-top: 16px; border-top: 1px solid var(--color-divider);">
              <button v-if="order.status === 'pending_payment'" class="btn-primary" @click="goPay">立即支付</button>
              <button v-if="order.status === 'pending_payment'" class="btn-ghost" style="margin-left: 12px;" @click="cancel">取消订单</button>
              <button class="btn-ghost" style="margin-left: 12px;" @click="$router.push('/orders')">返回列表</button>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // UserCenterPage
  // =========================================================================
  global.UserCenterPage = {
    name: 'UserCenterPage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const userStore = global.ShopStores.useUserStore();
      const router = useRouter();
      const view = ref('profile');

      onMounted(() => userStore.init());

      function goOrders(status) {
        router.push({ path: '/orders', query: status ? { status } : {} });
      }
      return { userStore, view, goOrders };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title"><h2>个人中心</h2></div>
          <div style="display: grid; grid-template-columns: 240px 1fr; gap: 24px;">
            <div class="cart-page" style="padding: 16px;">
              <div style="text-align: center; padding: 16px 0;">
                <div style="width: 64px; height: 64px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 28px; margin: 0 auto 12px;">
                  {{ (userStore.nickname || 'G').charAt(0).toUpperCase() }}
                </div>
                <div style="font-weight: 500;">{{ userStore.nickname }}</div>
                <div style="font-size: 12px; color: var(--color-text-tertiary); margin-top: 4px;">用户 ID: {{ userStore.userId }}</div>
              </div>
              <div style="border-top: 1px solid var(--color-divider); padding-top: 12px;">
                <div :style="['padding: 10px 12px; cursor: pointer; border-radius: 4px; margin-bottom: 4px;', view === 'profile' ? 'background: var(--color-primary-light); color: var(--color-primary);' : '']" @click="view = 'profile'">个人资料</div>
                <div :style="['padding: 10px 12px; cursor: pointer; border-radius: 4px; margin-bottom: 4px;', view === 'orders' ? 'background: var(--color-primary-light); color: var(--color-primary);' : '']" @click="view = 'orders'">我的订单</div>
                <div :style="['padding: 10px 12px; cursor: pointer; border-radius: 4px; margin-bottom: 4px;', view === 'addresses' ? 'background: var(--color-primary-light); color: var(--color-primary);' : '']" @click="view = 'addresses'">收货地址</div>
              </div>
            </div>
            <div class="cart-page">
              <div v-if="view === 'profile'">
                <h3 style="font-size: 16px; margin-bottom: 16px;">个人资料</h3>
                <div style="font-size: 14px; line-height: 2;">
                  <div>昵称: {{ userStore.nickname }}</div>
                  <div>用户 ID: {{ userStore.userId }}</div>
                  <div>电话: {{ userStore.phone || '未绑定' }}</div>
                </div>
              </div>
              <div v-else-if="view === 'orders'">
                <h3 style="font-size: 16px; margin-bottom: 16px;">我的订单</h3>
                <div class="order-status-grid">
                  <div class="order-status-item" @click="goOrders('pending_payment')">
                    <span class="order-status-dot order-status-dot-1">1</span>
                    <span class="order-status-label">待付款</span>
                  </div>
                  <div class="order-status-item" @click="goOrders('paid')">
                    <span class="order-status-dot order-status-dot-2">2</span>
                    <span class="order-status-label">待发货</span>
                  </div>
                  <div class="order-status-item" @click="goOrders('shipped')">
                    <span class="order-status-dot order-status-dot-3">3</span>
                    <span class="order-status-label">待收货</span>
                  </div>
                  <div class="order-status-item" @click="goOrders('completed')">
                    <span class="order-status-dot order-status-dot-4">4</span>
                    <span class="order-status-label">已完成</span>
                  </div>
                  <div class="order-status-item" @click="goOrders('')">
                    <span class="order-status-dot order-status-dot-5">5</span>
                    <span class="order-status-label">全部</span>
                  </div>
                </div>
              </div>
              <div v-else-if="view === 'addresses'">
                <h3 style="font-size: 16px; margin-bottom: 16px;">收货地址</h3>
                <button class="btn-primary" style="margin-bottom: 16px;" @click="$router.push('/user/addresses')">管理收货地址</button>
                <p style="color: var(--color-text-tertiary); font-size: 13px;">点击上方按钮添加或编辑地址</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // =========================================================================
  // AddressManagePage
  // =========================================================================
  global.AddressManagePage = {
    name: 'AddressManagePage',
    components: { EmptyState: global.EmptyState },
    setup() {
      const toast = global.ShopStores.useToastStore();
      const router = useRouter();
      const addresses = ref([]);
      const loading = ref(true);
      const editing = ref(null); // null = list view, {} = new, existing = edit
      const form = ref({ recipient: '', phone: '', province: '', city: '', district: '', detail: '', is_default: false });

      async function load() {
        loading.value = true;
        try {
          addresses.value = await global.ShopAPI.listAddresses();
        } catch (e) { toast.error(e.message); }
        finally { loading.value = false; }
      }
      function newAddress() {
        editing.value = {};
        form.value = { recipient: '', phone: '', province: '', city: '', district: '', detail: '', is_default: false };
      }
      function editAddress(a) {
        editing.value = a;
        form.value = { ...a };
      }
      async function save() {
        if (!form.value.recipient || !form.value.phone || !form.value.province || !form.value.city || !form.value.district || !form.value.detail) {
          toast.warning('请填写完整地址信息');
          return;
        }
        try {
          if (editing.value && editing.value.id) {
            await global.ShopAPI.updateAddress(editing.value.id, form.value);
            toast.success('地址已更新');
          } else {
            await global.ShopAPI.addAddress(form.value);
            toast.success('地址已添加');
          }
          editing.value = null;
          await load();
        } catch (e) { toast.error(e.message); }
      }
      async function remove(a) {
        if (!confirm('确定删除该地址？')) return;
        try {
          await global.ShopAPI.deleteAddress(a.id);
          toast.success('已删除');
          await load();
        } catch (e) { toast.error(e.message); }
      }
      function cancelEdit() { editing.value = null; }

      onMounted(load);
      return { addresses, loading, editing, form, newAddress, editAddress, save, remove, cancelEdit };
    },
    template: `
      <div class="container" style="padding-top: 16px;">
        <div class="page-section">
          <div class="section-title">
            <h2>收货地址管理</h2>
            <button v-if="!editing" class="btn-primary" @click="newAddress">+ 新增地址</button>
          </div>
          <div class="cart-page">
            <div v-if="editing">
              <h3 style="font-size: 16px; margin-bottom: 16px;">{{ editing.id ? '编辑地址' : '新增地址' }}</h3>
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="form-group">
                  <label class="form-label required">收件人</label>
                  <input v-model="form.recipient" class="form-input" placeholder="收件人姓名" />
                </div>
                <div class="form-group">
                  <label class="form-label required">手机号</label>
                  <input v-model="form.phone" class="form-input" placeholder="11 位手机号" />
                </div>
                <div class="form-group">
                  <label class="form-label required">省份</label>
                  <input v-model="form.province" class="form-input" placeholder="如：广东省" />
                </div>
                <div class="form-group">
                  <label class="form-label required">城市</label>
                  <input v-model="form.city" class="form-input" placeholder="如：深圳市" />
                </div>
                <div class="form-group">
                  <label class="form-label required">区/县</label>
                  <input v-model="form.district" class="form-input" placeholder="如：南山区" />
                </div>
                <div class="form-group">
                  <label class="form-label required">设为默认</label>
                  <label style="display: flex; align-items: center; gap: 8px; padding: 8px 0;">
                    <input type="checkbox" v-model="form.is_default" />
                    <span>设为默认地址</span>
                  </label>
                </div>
              </div>
              <div class="form-group">
                <label class="form-label required">详细地址</label>
                <textarea v-model="form.detail" class="form-textarea" rows="3" placeholder="街道、楼栋、门牌号等"></textarea>
              </div>
              <div style="display: flex; gap: 12px; margin-top: 16px;">
                <button class="btn-primary" @click="save">保存</button>
                <button class="btn-ghost" @click="cancelEdit">取消</button>
              </div>
            </div>
            <div v-else-if="loading">
              <div v-for="i in 2" :key="i" class="skeleton" style="height: 100px; margin-bottom: 12px;"></div>
            </div>
            <div v-else-if="addresses.length === 0">
              <empty-state icon="location" title="还没有收货地址" desc="添加一个收货地址以便下单">
                <button class="btn-primary" @click="newAddress">+ 新增地址</button>
              </empty-state>
            </div>
            <div v-else>
              <div v-for="addr in addresses" :key="addr.id"
                   style="padding: 16px; border: 1px solid var(--color-border); border-radius: 6px; margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                  <div>
                    <div>
                      <strong>{{ addr.recipient }}</strong>
                      <span style="margin-left: 12px; color: var(--color-text-tertiary);">{{ addr.phone }}</span>
                      <span v-if="addr.is_default" style="margin-left: 8px; padding: 2px 6px; background: var(--color-primary); color: white; font-size: 11px; border-radius: 3px;">默认</span>
                    </div>
                    <div style="margin-top: 4px; font-size: 13px; color: var(--color-text-secondary);">
                      {{ addr.province }}{{ addr.city }}{{ addr.district }}{{ addr.detail }}
                    </div>
                  </div>
                  <div>
                    <button class="btn-ghost" style="font-size: 12px; padding: 4px 12px; margin-right: 8px;" @click="editAddress(addr)">编辑</button>
                    <button class="btn-ghost" style="font-size: 12px; padding: 4px 12px; color: var(--color-danger);" @click="remove(addr)">删除</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `,
  };

  // Expose helpers for other components
  global.ShopUtils = { fmtPrice, fmtSales, fmtDate, STATUS_TEXT };
})(window);

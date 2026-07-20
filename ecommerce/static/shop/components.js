/* Shared Vue components used across pages — registered globally on the
 * `app` instance provided by app.js. Loaded BEFORE pages.js.
 */
(function (global) {
  const { computed, ref, onMounted } = Vue;

  // ----- AppHeader -----
  global.AppHeader = {
    name: 'AppHeader',
    setup() {
      const cartStore = global.ShopStores.useCartStore();
      const userStore = global.ShopStores.useUserStore();
      const router = VueRouter.useRouter();
      const route = VueRouter.useRoute();
      const keyword = ref(route.query.keyword || '');

      function search() {
        router.push({ path: '/search', query: keyword.value ? { keyword: keyword.value } : {} });
      }
      function goCart() { router.push('/cart'); }
      function goOrders() { router.push('/orders'); }
      function goUser() { router.push('/user'); }
      function goHome() { router.push('/'); }

      return { cartStore, userStore, keyword, search, goCart, goOrders, goUser, goHome };
    },
    template: `
      <header class="app-header">
        <div class="app-header-inner">
          <div class="app-logo" @click="goHome">
            <span class="app-logo-mark"></span>
            <span>优淘商城</span>
          </div>
          <div class="app-search">
            <input v-model="keyword" placeholder="搜索商品、品牌、类别..." @keyup.enter="search" />
            <button @click="search">搜索</button>
          </div>
          <nav class="app-nav">
            <div class="app-nav-item" @click="goOrders">
              <span class="app-nav-icon app-nav-icon-orders"></span>
              <span>订单</span>
            </div>
            <div class="app-nav-item" @click="goCart">
              <span class="app-nav-icon app-nav-icon-cart"></span>
              <span>购物车</span>
              <span v-if="cartStore.count > 0" class="app-nav-badge">{{ cartStore.count > 99 ? '99+' : cartStore.count }}</span>
            </div>
            <div class="app-nav-item" @click="goUser">
              <span class="app-nav-icon app-nav-icon-user"></span>
              <span>{{ userStore.nickname }}</span>
            </div>
          </nav>
        </div>
      </header>
    `,
  };

  // ----- CategoryBar -----
  global.CategoryBar = {
    name: 'CategoryBar',
    setup() {
      const catStore = global.ShopStores.useCategoryStore();
      const router = VueRouter.useRouter();
      onMounted(() => catStore.load());

      function goCategory(cat) {
        router.push({ path: '/category/' + cat.id, query: { name: cat.name } });
      }
      function goHome() { router.push('/'); }

      return { catStore, goCategory, goHome };
    },
    template: `
      <div class="category-bar">
        <div class="category-bar-inner">
          <div class="category-bar-item" @click="goHome">首页</div>
          <template v-for="cat in catStore.tree" :key="cat.id">
            <div class="category-bar-item" @click="goCategory(cat)">{{ cat.name }}</div>
          </template>
        </div>
      </div>
    `,
  };

  // ----- ProductCard -----
  global.ProductCard = {
    name: 'ProductCard',
    props: { product: { type: Object, required: true } },
    emits: ['click'],
    setup(props, { emit }) {
      function fmtPrice(p) {
        const n = Number(p);
        if (isNaN(n)) return '0';
        return n.toFixed(2);
      }
      function fmtSales(n) {
        if (n >= 10000) return (n / 10000).toFixed(1) + '万';
        return String(n);
      }
      // Discount % off the original price, shown as a red pill badge on
      // the image. Returns '' when no valid discount.
      function discountPercent(p) {
        const orig = Number(p.original_price);
        const cur = Number(p.price_min);
        if (!orig || !cur || orig <= cur) return '';
        const pct = Math.round((1 - cur / orig) * 100);
        return pct > 0 ? '-' + pct + '%' : '';
      }
      // Free-shipping pill: shows when price >= 99 (matches the banner
      // promo "全场满 99 元免邮"). Returns boolean.
      function hasFreeShip(p) {
        const cur = Number(p.price_min);
        return !isNaN(cur) && cur >= 99;
      }
      function onClick() { emit('click', props.product); }
      return { fmtPrice, fmtSales, discountPercent, hasFreeShip, onClick };
    },
    template: `
      <div class="product-card" @click="onClick">
        <div class="product-card-image">
          <img :src="product.main_image" :alt="product.title" loading="lazy" />
          <div class="product-card-tag">热销</div>
          <div v-if="discountPercent(product)" class="product-card-discount">{{ discountPercent(product) }}</div>
          <div v-if="hasFreeShip(product)" class="product-card-freeship">包邮</div>
        </div>
        <div class="product-card-info">
          <div class="product-card-title">{{ product.title }}</div>
          <div class="product-card-meta">
            <span class="product-card-price">
              <span class="product-card-price-symbol">¥</span>{{ fmtPrice(product.price_min) }}
            </span>
            <span v-if="product.original_price && Number(product.original_price) > Number(product.price_min)" class="product-card-original">
              ¥{{ fmtPrice(product.original_price) }}
            </span>
          </div>
          <div class="product-card-sales">
            <span class="product-card-rating">
              <span class="product-card-rating-star">★</span>{{ Number(product.rating_avg).toFixed(1) }}
            </span>
            <span class="product-card-sales-sep">·</span>
            <span>已售 {{ fmtSales(product.sales_count) }}</span>
          </div>
        </div>
      </div>
    `,
  };

  // ----- AppFooter -----
  global.AppFooter = {
    name: 'AppFooter',
    template: `
      <footer class="app-footer">
        <div class="container">
          <div class="app-footer-inner">
            <div class="app-footer-section">
              <h4>购物指南</h4>
              <a>购物流程</a>
              <a>会员介绍</a>
              <a>生活旅行</a>
              <a>常见问题</a>
            </div>
            <div class="app-footer-section">
              <h4>配送方式</h4>
              <a>上门自提</a>
              <a>211限时达</a>
              <a>配送服务查询</a>
              <a>配送费收取标准</a>
            </div>
            <div class="app-footer-section">
              <h4>支付方式</h4>
              <a>货到付款</a>
              <a>在线支付</a>
              <a>分期付款</a>
              <a>邮局汇款</a>
            </div>
            <div class="app-footer-section">
              <h4>售后服务</h4>
              <a>售后政策</a>
              <a>价格保护</a>
              <a>退款说明</a>
              <a>返修/退换货</a>
            </div>
          </div>
          <div class="app-footer-bottom">
            <p>© 2026 优淘商城 · 跨境电商平台 · 仅供学习演示</p>
          </div>
        </div>
      </footer>
    `,
  };

  // ----- ToastContainer -----
  global.ToastContainer = {
    name: 'ToastContainer',
    setup() {
      const toastStore = global.ShopStores.useToastStore();
      return { toastStore };
    },
    template: `
      <div class="toast-container">
        <div v-for="t in toastStore.items" :key="t.id" :class="['toast', t.type]">
          {{ t.message }}
        </div>
      </div>
    `,
  };

  // ----- CustomerServiceWidget (floating button + chat panel) -----
  // Visual language aligned with the standalone agent UI on port 8000:
  //   * AI avatar (anime-style image) on the left, user avatar on the right
  //   * Markdown rendering via marked + DOMPurify
  //   * Collapsible reasoning card + per-message feedback (thumb up/down)
  global.CustomerServiceWidget = {
    name: 'CustomerServiceWidget',
    setup() {
      const open = ref(false);
      const messages = ref([
        {
          role: 'ai',
          text: '您好，我是您的专属 AI 客服助手。\n\n我可以为您：\n- **查询订单状态**\n- **处理退款申请**\n- **解答商品咨询**\n- **提供售后支持**\n\n请问有什么可以帮您？',
          trace_id: null,
          feedback: null, // 'up' | 'down' | null
        },
      ]);
      const input = ref('');
      const sending = ref(false);
      const threadId = ref(global.ShopAPI.getSessionId());
      const router = VueRouter.useRouter();
      const route = VueRouter.useRoute();
      const toast = global.ShopStores.useToastStore();
      const messagesContainer = ref(null);
      const avatarUrl = '/shop/static/avatars/ai_avatar.jpg';
      const avatarError = ref(false);

      // Configure marked once: GFM line breaks, safe defaults.
      if (window.marked) {
        window.marked.setOptions({ gfm: true, breaks: true });
      }

      function renderMarkdown(text) {
        if (!text) return '';
        try {
          const raw = window.marked ? window.marked.parse(text) : text;
          return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
        } catch (e) {
          return text;
        }
      }

      function toggle() {
        open.value = !open.value;
        if (open.value) {
          setTimeout(() => scrollToBottom(), 100);
        }
      }

      function scrollToBottom() {
        if (messagesContainer.value) {
          messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
        }
      }

      function buildContext() {
        const ctx = {};
        const path = route.path;
        if (path.startsWith('/product/')) {
          ctx.current_page = '商品详情页';
          ctx.product_id = path.split('/').pop();
        } else if (path === '/cart') {
          ctx.current_page = '购物车';
        } else if (path.startsWith('/order/')) {
          ctx.current_page = '订单详情';
          ctx.order_id = path.split('/').pop();
        } else if (path === '/orders') {
          ctx.current_page = '我的订单';
        } else if (path === '/') {
          ctx.current_page = '首页';
        }
        return ctx;
      }

      async function send() {
        const text = input.value.trim();
        if (!text || sending.value) return;
        input.value = '';
        messages.value.push({ role: 'user', text, feedback: null });
        // AI message placeholder: typing=true, reasoning=[] (will be filled
        // live as SSE step_start events arrive), text='' (filled by `final`).
        const aiMsg = {
          role: 'ai',
          text: '',
          trace_id: null,
          feedback: null,
          typing: true,
          reasoning: [],
          summary: null,
          expanded: true, // user can collapse the card after the answer arrives
        };
        messages.value.push(aiMsg);
        sending.value = true;
        await nextTickScroll();

        const ctx = buildContext();
        let pendingRouteStep = null; // route event arrives AFTER router step_end; we patch it in.

        const controller = global.ShopAPI.chatWithAgentStream(
          text,
          threadId.value,
          ctx,
          (event, data) => {
            // Route: router decided which subagent handles this. Annotate
            // the previous router step if present, otherwise push a new one.
            if (event === 'meta') {
              if (data.thread_id) threadId.value = data.thread_id;
              return;
            }
            if (event === 'route') {
              const sub = data.subagent_name || data.route || '';
              const reason = data.route_reason || '';
              // The router step_start was already pushed (agent_think).
              // Find the last running step and annotate it.
              const last = aiMsg.reasoning[aiMsg.reasoning.length - 1];
              if (last && last.status === 'running' && last.step_type === 'agent_think') {
                last.route_info = {
                  route: data.route,
                  route_reason: reason,
                  subagent_name: sub,
                };
                if (sub) {
                  last.friendly_message = `已转交「${subAgentLabel(sub)}」处理`;
                } else if (reason) {
                  last.friendly_message = reason;
                }
              } else {
                // No router step pushed yet (edge case) — push one.
                aiMsg.reasoning.push({
                  step_id: 'route-' + Date.now(),
                  step_type: 'agent_think',
                  friendly_message: sub ? `已转交「${subAgentLabel(sub)}」处理` : reason || '路由决策',
                  status: 'done',
                  latency_ms: null,
                  route_info: { route: data.route, route_reason: reason, subagent_name: sub },
                });
              }
              return;
            }
            if (event === 'step_start') {
              aiMsg.reasoning.push({
                step_id: data.step_id,
                step_type: data.step_type || 'step',
                friendly_message: data.friendly_message || friendlyForStepType(data.step_type),
                status: 'running',
                latency_ms: null,
                preview: null,
                tool_name: data.tool_name || null,
              });
              nextTickScroll();
              return;
            }
            if (event === 'step_end') {
              const step = aiMsg.reasoning.find((s) => s.step_id === data.step_id);
              if (step) {
                step.status = 'done';
                step.latency_ms = data.latency_ms;
                step.preview = data.preview || '';
              }
              nextTickScroll();
              return;
            }
            if (event === 'token') {
              // Optional: stream text into the bubble as tokens arrive.
              // We DON'T do this because the `final` event carries the
              // complete answer and we already have a typing indicator.
              // Streaming partial text would conflict with the typing dot.
              return;
            }
            if (event === 'final') {
              aiMsg.text = data.answer || '';
              aiMsg.trace_id = data.trace_id || null;
              aiMsg.typing = false;
              // Auto-collapse the reasoning card once the answer is in,
              // so the answer takes focus. User can re-expand to review.
              aiMsg.expanded = false;
              sending.value = false;
              nextTickScroll();
              return;
            }
            if (event === 'summary') {
              aiMsg.summary = {
                total_latency_ms: data.total_latency_ms,
                num_tools_called: data.num_tools_called,
                num_llm_calls: data.num_llm_calls,
                num_steps: data.num_steps,
                ok: data.ok,
              };
              // If any step is still 'running' (e.g. agent errored mid-step),
              // mark it done so the UI doesn't show a stuck spinner.
              aiMsg.reasoning.forEach((s) => {
                if (s.status === 'running') s.status = 'done';
              });
              nextTickScroll();
              return;
            }
            if (event === 'error') {
              aiMsg.text = '抱歉，客服系统暂时不可用：' + (data.message || '未知错误')
                + '\n\n请稍后再试，或拨打客服热线 **400-000-0000**';
              aiMsg.typing = false;
              aiMsg.reasoning.forEach((s) => {
                if (s.status === 'running') s.status = 'done';
              });
              sending.value = false;
              toast.error('客服请求失败');
              nextTickScroll();
              return;
            }
          },
        );
        // Stash the controller so closing the panel mid-stream can abort.
        aiMsg._controller = controller;
      }

      function friendlyForStepType(t) {
        if (t === 'tool_call') return '正在调用工具...';
        if (t === 'llm_call') return '正在分析并生成回复...';
        if (t === 'agent_think') return '正在思考...';
        return '处理中...';
      }

      function subAgentLabel(name) {
        const map = { order_ops: '订单专员', knowledge: '知识库专员', escalation: '人工客服', router: '路由判断' };
        return map[name] || name || '专员';
      }

      function toggleReasoning(msg) {
        msg.expanded = !msg.expanded;
      }

      async function sendFeedback(message, type) {
        if (!message.trace_id) {
          toast.info('该消息暂不支持反馈');
          return;
        }
        try {
          await global.ShopAPI.sendAgentFeedback(message.trace_id, type);
          message.feedback = type;
          toast.success(type === 'up' ? '感谢好评！' : '已记录您的反馈');
        } catch (e) {
          toast.error('反馈提交失败');
        }
      }

      function nextTickScroll() {
        return new Promise((resolve) => {
          setTimeout(() => { scrollToBottom(); resolve(); }, 50);
        });
      }

      return {
        open, messages, input, sending, messagesContainer,
        avatarUrl, avatarError,
        toggle, send, sendFeedback, renderMarkdown, toggleReasoning,
      };
    },
    template: `
      <div class="cs-widget">
        <button v-if="!open" class="cs-launcher" @click="toggle" title="联系客服">
          <span class="cs-launcher-icon"></span>
          <span class="cs-launcher-pulse"></span>
        </button>
        <div v-if="open" class="cs-panel">
          <div class="cs-panel-header">
            <div class="cs-panel-header-info">
              <img v-if="!avatarError" :src="avatarUrl" class="cs-panel-header-avatar" alt="AI" @error="avatarError = true" />
              <div v-else class="cs-panel-header-avatar cs-panel-header-avatar-fallback">AI</div>
              <div>
                <div class="cs-panel-title">智能客服</div>
                <div class="cs-panel-subtitle">AI 自动回复 · 7×24 在线</div>
              </div>
            </div>
            <button class="cs-panel-close" @click="toggle" title="收起">×</button>
          </div>
          <div class="cs-messages" ref="messagesContainer">
            <div v-for="(m, i) in messages" :key="i" :class="['cs-message', m.role === 'user' ? 'cs-message-self' : 'cs-message-ai']">
              <img v-if="m.role === 'ai' && !avatarError" :src="avatarUrl" class="cs-msg-avatar" alt="AI" @error="avatarError = true" />
              <div v-else-if="m.role === 'ai'" class="cs-msg-avatar cs-msg-avatar-fallback">AI</div>
              <div v-else class="cs-msg-avatar cs-msg-avatar-user">我</div>
              <div class="cs-msg-content">
                <!-- Reasoning card: live thinking steps streamed via SSE -->
                <div v-if="m.reasoning && m.reasoning.length > 0" class="cs-reasoning" :class="{ 'cs-reasoning-collapsed': !m.expanded }">
                  <div class="cs-reasoning-header" @click="toggleReasoning(m)">
                    <span class="cs-reasoning-icon">{{ m.typing ? '🔄' : '🧠' }}</span>
                    <span class="cs-reasoning-title">{{ m.typing ? '正在思考' : '思考过程' }}</span>
                    <span class="cs-reasoning-meta">
                      <span class="cs-reasoning-step-count">{{ m.reasoning.length }} 步</span>
                      <span v-if="m.summary && m.summary.total_latency_ms" class="cs-reasoning-latency">
                        · {{ (m.summary.total_latency_ms / 1000).toFixed(1) }}s
                      </span>
                      <span v-else-if="m.typing" class="cs-reasoning-live-dot"></span>
                    </span>
                    <span class="cs-reasoning-toggle">{{ m.expanded ? '▾' : '▸' }}</span>
                  </div>
                  <div v-if="m.expanded" class="cs-reasoning-body">
                    <div v-for="(s, si) in m.reasoning" :key="s.step_id || si" class="cs-reasoning-step" :class="'cs-reasoning-step-' + s.status">
                      <span class="cs-reasoning-step-icon">
                        <span v-if="s.status === 'running'" class="cs-spinner"></span>
                        <span v-else>✓</span>
                      </span>
                      <div class="cs-reasoning-step-main">
                        <div class="cs-reasoning-step-row">
                          <span class="cs-reasoning-step-msg">{{ s.friendly_message }}</span>
                          <span v-if="s.latency_ms != null" class="cs-reasoning-step-latency">{{ s.latency_ms }}ms</span>
                        </div>
                        <div v-if="s.route_info && s.route_info.subagent_name" class="cs-reasoning-step-route">
                          路由: {{ s.route_info.subagent_name }}
                          <span v-if="s.route_info.route_reason"> · {{ s.route_info.route_reason }}</span>
                        </div>
                        <div v-if="s.tool_name" class="cs-reasoning-step-tool">
                          <span class="cs-reasoning-tag cs-reasoning-tag-tool">tool</span>
                          <span class="cs-reasoning-tool-name">{{ s.tool_name }}</span>
                        </div>
                      </div>
                    </div>
                    <div v-if="m.summary" class="cs-reasoning-summary">
                      <span>工具调用 {{ m.summary.num_tools_called || 0 }}</span>
                      <span>·</span>
                      <span>LLM 调用 {{ m.summary.num_llm_calls || 0 }}</span>
                      <span>·</span>
                      <span :class="m.summary.ok ? 'cs-reasoning-ok' : 'cs-reasoning-fail'">
                        {{ m.summary.ok ? '成功' : '失败' }}
                      </span>
                    </div>
                  </div>
                </div>
                <div v-if="m.typing && m.reasoning.length === 0" class="cs-message-bubble cs-typing">
                  <span class="cs-dot"></span><span class="cs-dot"></span><span class="cs-dot"></span>
                </div>
                <div v-else-if="m.text" class="cs-message-bubble" v-html="renderMarkdown(m.text)"></div>
                <div v-if="m.trace_id && !m.typing" class="cs-msg-actions">
                  <button
                    :class="['cs-feedback-btn', m.feedback === 'up' ? 'cs-feedback-active' : '']"
                    @click="sendFeedback(m, 'up')"
                    title="有帮助"
                  ><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></button>
                  <button
                    :class="['cs-feedback-btn', m.feedback === 'down' ? 'cs-feedback-active' : '']"
                    @click="sendFeedback(m, 'down')"
                    title="没帮助"
                  ><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg></button>
                </div>
              </div>
            </div>
          </div>
          <div class="cs-input-row">
            <input
              v-model="input"
              placeholder="输入您的问题..."
              @keyup.enter="send"
              :disabled="sending"
            />
            <button class="cs-send-btn" @click="send" :disabled="sending || !input.trim()">
              {{ sending ? '发送中...' : '发送' }}
            </button>
          </div>
        </div>
      </div>
    `,
  };

  // ----- Pagination -----
  global.Pagination = {
    name: 'Pagination',
    props: {
      page: { type: Number, required: true },
      pageSize: { type: Number, default: 20 },
      total: { type: Number, required: true },
    },
    emits: ['change'],
    setup(props, { emit }) {
      const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)));
      const pages = computed(() => {
        const arr = [];
        const tp = totalPages.value;
        const cp = props.page;
        let s = Math.max(1, cp - 2);
        let e = Math.min(tp, s + 4);
        s = Math.max(1, e - 4);
        for (let i = s; i <= e; i++) arr.push(i);
        return arr;
      });
      function go(p) {
        if (p < 1 || p > totalPages.value || p === props.page) return;
        emit('change', p);
      }
      return { totalPages, pages, go };
    },
    template: `
      <div class="pagination" v-if="total > 0">
        <button @click="go(1)" :disabled="page === 1">首页</button>
        <button @click="go(page - 1)" :disabled="page === 1">‹</button>
        <button v-for="p in pages" :key="p" @click="go(p)" :class="{ active: p === page }">{{ p }}</button>
        <button @click="go(page + 1)" :disabled="page === totalPages">›</button>
        <button @click="go(totalPages)" :disabled="page === totalPages">末页</button>
      </div>
    `,
  };

  // ----- EmptyState -----
  // Uses a CSS-drawn geometric icon (rounded square with internal lines)
  // instead of an emoji — keeps the premium look consistent with the
  // Apple/Google-inspired visual language. The `icon` prop is now a
  // type key ('box' | 'cart' | 'search' | 'check') selecting which CSS
  // glyph to draw; legacy emoji values are silently ignored.
  global.EmptyState = {
    name: 'EmptyState',
    props: {
      icon: { type: String, default: 'box' },
      title: { type: String, default: '暂无数据' },
      desc: { type: String, default: '' },
    },
    computed: {
      iconClass() {
        // Map legacy emoji inputs to the new type keys so old call sites
        // keep rendering something sensible.
        const map = { '📦': 'box', '🛒': 'cart', '✅': 'check', '📍': 'location' };
        return 'empty-state-icon-' + (map[this.icon] || (['box','cart','search','check','location'].includes(this.icon) ? this.icon : 'box'));
      },
    },
    template: `
      <div class="empty-state">
        <div :class="['empty-state-icon', iconClass]"></div>
        <div class="empty-state-text">{{ title }}</div>
        <div v-if="desc" style="font-size: 12px; color: var(--color-text-muted); margin-bottom: 16px;">{{ desc }}</div>
        <slot></slot>
      </div>
    `,
  };
})(window);

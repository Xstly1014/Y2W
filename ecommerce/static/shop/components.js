/* Shared Vue components used across pages — registered globally on the
 * `app` instance provided by app.js. Loaded BEFORE pages.js.
 */
(function (global) {
  const { computed, ref, shallowRef, markRaw, onMounted, onBeforeUnmount } = Vue;

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
      // Kiki-style: input mode selector (界面模式 / 编程模式). Drives a
      // separate system prompt hint passed in `context.mode` so the
      // backend can tune its answer style. The choice is per-session
      // and stored in localStorage so it persists across reloads.
      const MODES = [
        { id: 'ui',   label: '界面模式', icon: 'ui',   hint: '面向用户' },
        { id: 'code', label: '编程模式', icon: 'code', hint: '技术细节' },
      ];
      const initialModeId = (() => {
        try { return localStorage.getItem('cs.mode') || 'ui'; } catch (e) { return 'ui'; }
      })();
      const mode = ref(initialModeId);
      const modeMenuOpen = ref(false);
      // Close the mode dropdown on outside click — bound at the panel
      // root so taps anywhere else dismiss it.
      function closeModeMenu() { modeMenuOpen.value = false; }
      const currentMode = computed(() => MODES.find((m) => m.id === mode.value) || MODES[0]);
      function toggleModeMenu() { modeMenuOpen.value = !modeMenuOpen.value; }
      function selectMode(m) {
        setMode(m);
        modeMenuOpen.value = false;
      }
      function setMode(m) {
        mode.value = m;
        try { localStorage.setItem('cs.mode', m); } catch (e) {}
      }
      // Kiki-style: dark mode toggle. Toggles a class on the panel so
      // styles can switch palettes. Persisted.
      const dark = ref((() => {
        try { return localStorage.getItem('cs.dark') === '1'; } catch (e) { return false; }
      })());
      function toggleDark() {
        dark.value = !dark.value;
        try { localStorage.setItem('cs.dark', dark.value ? '1' : '0'); } catch (e) {}
      }
      // Kiki-style welcome: title + subtitle + suggested question chips.
      // The chips are clickable and pre-fill the input on click so users
      // can start a conversation with one tap (same UX as the reference
      // Tencent Cloud assistant panel).
      const SUGGESTIONS = [
        { id: 'order',   label: '查询我的订单',            prompt: '我想查询我的订单状态' },
        { id: 'refund',  label: '如何申请退款',            prompt: '请告诉我如何申请退款' },
        { id: 'product', label: '帮我推荐一款商品',        prompt: '能帮我推荐一款热销商品吗' },
        { id: 'aftersale', label: '售后服务说明',           prompt: '请介绍你们的售后服务政策' },
      ];
      const messages = ref([
        {
          role: 'ai',
          kind: 'welcome', // signals the renderer to use the hero greeting layout
          title: '您好，我是您的 AI 智能助手',
          subtitle: '可以为您查询订单、推荐商品、处理售后，有什么需要请告诉我～',
          suggestions: SUGGESTIONS,
          trace_id: null,
          feedback: null,
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
          const raw = window.marked ? window.marked.parse(text) : raw_text_safe(text);
          // When DOMPurify is unavailable, fall back to the escaped plain
          // text rather than the marked HTML output — marked's escaping
          // is not a security guarantee. See P2-8.
          return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw_text_safe(text);
        } catch (e) {
          return raw_text_safe(text);
        }
      }
      // Fallback when marked is unavailable: escape HTML and convert
      // newlines to <br>. Keeps the answer readable without rendering
      // raw user input as HTML.
      function raw_text_safe(text) {
        const esc = String(text)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
        return esc.replace(/\n/g, '<br>');
      }

      function toggle() {
        open.value = !open.value;
        if (open.value) {
          setTimeout(() => scrollToBottom(), 100);
        }
      }

      // ---------------------------------------------------------------
      // Drag-to-move the panel by its header. Tracks pointermove and
      // updates the panel's top/left. Bottom + right are released
      // once the user starts dragging so the panel follows the
      // cursor from the very first pixel. Clamped to the viewport
      // so the title bar (which doubles as the drag handle) is
      // always grabbable.
      // ---------------------------------------------------------------
      const panelPos = ref({ left: null, top: null });
      const dragging = ref(false);
      const widgetRoot = ref(null);
      function startDrag(e) {
        // Ignore right-clicks and clicks on the action buttons.
        if (e.button !== 0) return;
        if (e.target.closest && e.target.closest('.cs-panel-icon-btn')) return;
        dragging.value = true;
        const rect = (widgetRoot.value || document.querySelector('.cs-widget'))
          .querySelector('.cs-panel').getBoundingClientRect();
        // Switch the panel to left/top positioning so the cursor
        // offset stays consistent for the rest of the drag.
        panelPos.value = { left: rect.left, top: rect.top };
        document.body.style.userSelect = 'none';
        e.preventDefault();
      }
      function onDrag(e) {
        if (!dragging.value) return;
        const w = window.innerWidth;
        const h = window.innerHeight;
        const panelEl = widgetRoot.value && widgetRoot.value.querySelector('.cs-panel');
        if (!panelEl) return;
        const pw = panelEl.offsetWidth;
        const ph = panelEl.offsetHeight;
        // Clamp so the panel can never be dragged fully off-screen
        // (keep at least 80px of the header grabbable).
        const nx = Math.max(0, Math.min(w - 80, e.clientX - pw / 2));
        const ny = Math.max(0, Math.min(h - 40, e.clientY - 20));
        panelPos.value = { left: nx, top: ny };
      }
      function endDrag() {
        if (!dragging.value) return;
        dragging.value = false;
        document.body.style.userSelect = '';
      }
      // Bind globally so the user can drag fast and the cursor leaves
      // the header — the drag still follows.
      onMounted(() => {
        window.addEventListener('pointermove', onDrag);
        window.addEventListener('pointerup', endDrag);
        window.addEventListener('pointercancel', endDrag);
        // Click-outside closes the mode dropdown. Bound on the capture
        // phase so taps on the chip itself don't immediately close the
        // menu they just opened.
        window.addEventListener('click', (e) => {
          if (!modeMenuOpen.value) return;
          if (e.target && e.target.closest && e.target.closest('.cs-mode-chip-wrap')) return;
          modeMenuOpen.value = false;
        });
      });
      onBeforeUnmount(() => {
        window.removeEventListener('pointermove', onDrag);
        window.removeEventListener('pointerup', endDrag);
        window.removeEventListener('pointercancel', endDrag);
      });
      // Computed style binding: when the user has dragged the panel
      // once, switch from bottom/right anchoring to left/top.
      const panelStyle = computed(() => {
        if (panelPos.value.left == null) return {};
        return { left: panelPos.value.left + 'px', top: panelPos.value.top + 'px', right: 'auto', bottom: 'auto' };
      });

      // ---------------------------------------------------------------
      // New conversation — wipes the message list and resets the
      // thread id so the next message starts a fresh agent run. The
      // welcome card is re-seeded so the panel doesn't look empty.
      // ---------------------------------------------------------------
      function startNewConversation() {
        // Abort any in-flight SSE so the old run doesn't keep mutating
        // the new messages array.
        for (const m of messages.value) {
          if (m._controller && m._controller.abort) m._controller.abort();
        }
        threadId.value = global.ShopAPI.getSessionId() + '-r' + Date.now().toString(36);
        messages.value = [
          {
            role: 'ai', kind: 'welcome',
            title: '您好，我是您的 AI 智能助手',
            subtitle: '可以为您查询订单、推荐商品、处理售后，有什么需要请告诉我～',
            suggestions: SUGGESTIONS,
            trace_id: null, feedback: null,
          },
        ];
        // Clear pending attachments — they belong to the old thread.
        attachments.value = [];
        input.value = '';
        setTimeout(() => scrollToBottom(), 50);
      }

      // ---------------------------------------------------------------
      // File upload — paperclip opens the file picker, drag-and-drop
      // onto the input bar also adds files. Each file is previewed
      // as a small chip with name + size + remove button. On send
      // we build a FormData and POST to /api/customer-service/chat
      // (multipart); the agent receives the text + files in one
      // request. If the backend doesn't support attachments we fall
      // back to the regular JSON endpoint and warn via toast.
      // ---------------------------------------------------------------
      const attachments = shallowRef([]);
      const fileInput = ref(null);
      const inputBar = ref(null);
      const ACCEPT = 'image/*,application/pdf,text/plain,.md,.json,.csv,.log';
      const MAX_SIZE = 5 * 1024 * 1024; // 5 MB per file
      function pickFiles() {
        if (fileInput.value) fileInput.value.click();
      }
      function onFilesPicked(e) {
        const list = e.target.files;
        if (!list || !list.length) return;
        addFiles(Array.from(list));
        // Reset so picking the same file twice still fires.
        e.target.value = '';
      }
      function addFiles(files) {
        for (const f of files) {
          if (f.size > MAX_SIZE) {
            toast.error(f.name + ' 超过 5MB，已跳过');
            continue;
          }
          // markRaw() prevents Vue from wrapping the File in a reactive
          // Proxy. Without this, FormData.append() rejects the value with
          // "parameter 2 is not of type 'Blob'" because a Proxy is not a
          // Blob, even though the underlying target is a File (which IS
          // a Blob). markRaw is the canonical Vue 3 fix.
          const att = markRaw({
            id: 'att-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
            name: f.name,
            size: f.size,
            type: f.type,
            file: f,
            preview: null,
          });
          // Generate an image preview data URL so the chip can show
          // a tiny thumbnail.
          if (f.type && f.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (ev) => { att.preview = ev.target.result; };
            reader.readAsDataURL(f);
          }
          attachments.value.push(att);
        }
      }
      function removeAttachment(id) {
        attachments.value = attachments.value.filter((a) => a.id !== id);
      }
      // Drag & drop on the input bar.
      function onInputDragOver(e) {
        if (e.dataTransfer && Array.from(e.dataTransfer.items || []).some((i) => i.kind === 'file')) {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'copy';
        }
      }
      function onInputDrop(e) {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        addFiles(Array.from(e.dataTransfer.files));
      }
      function fmtSize(n) {
        if (n < 1024) return n + 'B';
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'KB';
        return (n / 1024 / 1024).toFixed(2) + 'MB';
      }
      // Auto-grow the textarea up to ~5 lines, then stop.
      function autoResize(e) {
        const el = e && e.target ? e.target : null;
        if (!el) return;
        el.style.height = 'auto';
        const max = 5 * 22; // ~5 lines at 22px line-height
        el.style.height = Math.min(el.scrollHeight, max) + 'px';
      }

      function scrollToBottom() {
        if (messagesContainer.value) {
          messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
        }
      }

      function applySuggestion(s) {
        // Pre-fill the input + auto-send. Mirrors the Kiki "tap a chip
        // to start" interaction.
        if (sending.value) return;
        input.value = s.prompt;
        send();
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
        // Kiki-style: forward the active mode (界面模式 / 编程模式) so the
        // backend can adapt its response style.
        ctx.mode = mode.value;
        return ctx;
      }

      async function send() {
        const text = input.value.trim();
        const hasFiles = attachments.value.length > 0;
        if ((!text && !hasFiles) || sending.value) return;
        input.value = '';
        // Snapshot the attachments and clear them from the bar so the
        // user can immediately start typing the next message.
        const pendingFiles = attachments.value.slice();
        attachments.value = [];
        // Once the user sends their first message, the welcome card is
        // superseded by the new conversation. We DON'T remove it (keeps
        // history readable) but the renderer no longer shows the chip
        // row once conversation has started.
        // The user message bubble shows attached file names inline so
        // the conversation history reads correctly.
        const userMsg = { role: 'user', text, feedback: null };
        if (pendingFiles.length) {
          userMsg.attachments = pendingFiles.map((a) => ({ id: a.id, name: a.name, size: a.size, type: a.type, preview: a.preview }));
        }
        messages.value.push(userMsg);
        // AI message placeholder: typing=true, reasoning=[] (will be filled
        // live as SSE step_start events arrive), text='' (filled by `final`).
        const aiMsg = {
          role: 'ai',
          kind: 'answer',
          text: '',
          trace_id: null,
          feedback: null,
          typing: true,
          reasoning: [],
          summary: null,
          reasoningExpanded: false, // Kiki-style: collapsed by default
          // Kiki-style: answer attachments populated on `final`:
          followups: null,        // ["你可以继续：" chips array]
          primaryAction: null,    // {id,label,icon,prompt}  -> gradient button
          // Kiki multi-turn: mid-conversation AI bubbles emitted
          // before tool calls (interim_answer SSE event). Each entry
          // is a markdown string rendered as a small bubble between
          // the reasoning card and the final answer.
          interimAnswers: [],
          // Kiki action cards: clickable buttons rendered after the
          // final answer. Collected from action_card SSE events and
          // the final event's action_cards field.
          actionCards: [],
        };
        messages.value.push(aiMsg);
        sending.value = true;
        await nextTickScroll();

        const ctx = buildContext();
        let pendingRouteStep = null; // route event arrives AFTER router step_end; we patch it in.

        // Pick the streaming call: with or without attachments. The
        // attachment-aware path POSTs a multipart/form-data request so
        // the backend can ingest the file bytes alongside the prompt.
        const streamFn = hasFiles
          ? (global.ShopAPI.chatWithAgentStreamAttachments || global.ShopAPI.chatWithAgentStream)
          : global.ShopAPI.chatWithAgentStream;

        const handleEvent = (event, data) => {
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
            if (event === 'interim_answer') {
              // Kiki multi-turn: a mid-conversation AI bubble emitted
              // when the LLM outputs visible text alongside a tool_call.
              // Render it immediately as a small bubble so the user sees
              // the agent's narration BEFORE the tool runs. Multiple
              // interim answers can appear in a single turn.
              if (data && data.answer) {
                aiMsg.interimAnswers.push(data.answer);
                nextTickScroll();
              }
              return;
            }
            if (event === 'action_card') {
              // Kiki action card: a clickable button emitted separately
              // from `final`. Collect now; rendered after the final answer.
              if (data && data.id && data.label && data.prompt) {
                aiMsg.actionCards.push({
                  id: data.id,
                  label: data.label,
                  prompt: data.prompt,
                });
              }
              return;
            }
            if (event === 'final') {
              aiMsg.text = data.answer || '';
              aiMsg.trace_id = data.trace_id || null;
              aiMsg.typing = false;
              // Auto-collapse the reasoning card once the answer is in,
              // so the answer takes focus. User can re-expand to review.
              aiMsg.reasoningExpanded = false;
              // Backend may also attach action_cards to the final event
              // (batch). Merge with any already collected from streaming.
              if (Array.isArray(data.action_cards) && data.action_cards.length) {
                const seen = new Set(aiMsg.actionCards.map((c) => c.id));
                data.action_cards.forEach((c) => {
                  if (c && c.id && c.label && c.prompt && !seen.has(c.id)) {
                    aiMsg.actionCards.push({ id: c.id, label: c.label, prompt: c.prompt });
                  }
                });
              }
              // Kiki-style follow-up chips: shown right under the answer
              // card. Backend may attach `followups`; if absent, pick a
              // small set of generic follow-ups based on the route.
              aiMsg.followups = buildFollowUps(aiMsg, data);
              // Kiki-style primary action button: only for action-oriented
              // routes (e.g. "申请退款" / "查看订单") where the agent can
              // actually take the next step. Falls back to a "重新提问"
              // variant for Q&A routes.
              aiMsg.primaryAction = buildPrimaryAction(aiMsg, data);
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
        };
        // Dispatch to the right streaming call. The attachment-aware
        // path takes (text, threadId, ctx, files, onEvent) while the
        // text-only path is (text, threadId, ctx, onEvent).
        const controller = hasFiles
          ? streamFn(text, threadId.value, ctx, pendingFiles, handleEvent)
          : streamFn(text, threadId.value, ctx, handleEvent);
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

      // ---------------------------------------------------------------
      // Kiki-style: follow-up chips + primary action button
      // ---------------------------------------------------------------
      // Picks 2-3 follow-up suggestions to show under the answer.
      // Backend may provide these via `data.followups`; if not, we
      // fall back to a small set of generic chips based on which
      // subagent handled the question.
      function buildFollowUps(aiMsg, data) {
        if (Array.isArray(data.followups) && data.followups.length) {
          return data.followups.slice(0, 4);
        }
        // Detect route from the last reasoning step.
        let route = '';
        for (let i = aiMsg.reasoning.length - 1; i >= 0; i--) {
          const r = aiMsg.reasoning[i].route_info;
          if (r && r.subagent_name) { route = r.subagent_name; break; }
        }
        if (route === 'order_ops') {
          return [
            { id: 'fu1', label: '查看我的订单',   prompt: '帮我查看最近的订单' },
            { id: 'fu2', label: '申请退款',       prompt: '我想申请退款' },
            { id: 'fu3', label: '修改收货地址',   prompt: '如何修改收货地址' },
          ];
        }
        if (route === 'knowledge') {
          return [
            { id: 'fu1', label: '推荐热销商品',   prompt: '推荐一款热销商品' },
            { id: 'fu2', label: '了解售后政策',   prompt: '售后政策是怎样的' },
            { id: 'fu3', label: '查看产品文档',   prompt: '给我看看产品说明' },
          ];
        }
        // Generic fallback.
        return [
          { id: 'fu1', label: '推荐热销商品',   prompt: '有什么热销商品' },
          { id: 'fu2', label: '查询我的订单',   prompt: '查询我的订单状态' },
          { id: 'fu3', label: '联系人工客服',   prompt: '请转人工客服' },
        ];
      }
      // Picks the big gradient "primary action" button (Kiki's "帮我操作"
      // style). Returns null when no obvious action exists.
      function buildPrimaryAction(aiMsg, data) {
        if (data.primary_action) return data.primary_action;
        let route = '';
        for (let i = aiMsg.reasoning.length - 1; i >= 0; i--) {
          const r = aiMsg.reasoning[i].route_info;
          if (r && r.subagent_name) { route = r.subagent_name; break; }
        }
        if (route === 'order_ops') {
          return { id: 'view-orders', label: '查看我的订单', icon: 'orders', prompt: '打开我的订单列表' };
        }
        if (route === 'knowledge') {
          return { id: 'recommend', label: '帮我推荐', icon: 'sparkle', prompt: '帮我推荐一款合适的商品' };
        }
        if (route === 'escalation') {
          return { id: 'human', label: '转接人工客服', icon: 'headset', prompt: '请转人工客服' };
        }
        return null;
      }
      function runPrimaryAction(action) {
        if (!action || sending.value) return;
        if (action.prompt) {
          input.value = action.prompt;
          send();
        } else if (action.href) {
          window.location.href = action.href;
        }
      }
      function runFollowUp(fu) {
        if (sending.value) return;
        input.value = fu.prompt;
        send();
      }

      function toggleReasoning(msg) {
        msg.reasoningExpanded = !msg.reasoningExpanded;
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
        mode, MODES, currentMode, modeMenuOpen, toggleModeMenu, selectMode, closeModeMenu, setMode, dark, toggleDark,
        toggle, send, sendFeedback, renderMarkdown, toggleReasoning,
        applySuggestion, runFollowUp, runPrimaryAction,
        // Drag-to-move
        widgetRoot, panelStyle, startDrag, dragging,
        // New conversation
        startNewConversation,
        // File upload
        attachments, fileInput, inputBar, ACCEPT, MAX_SIZE,
        pickFiles, onFilesPicked, addFiles, removeAttachment,
        onInputDragOver, onInputDrop, fmtSize, autoResize,
      };
    },
    template: `
      <div class="cs-widget" ref="widgetRoot">
        <button v-if="!open" class="cs-launcher" @click="toggle" title="联系客服">
          <span class="cs-launcher-icon"></span>
          <span class="cs-launcher-pulse"></span>
        </button>
        <div
          v-if="open"
          :class="['cs-panel', dark ? 'cs-panel-dark' : '', dragging ? 'cs-panel-dragging' : '']"
          :style="panelStyle"
        >
          <div class="cs-panel-header" @pointerdown="startDrag" :class="{ 'cs-panel-header-draggable': true }" title="按住拖动窗口">
            <div class="cs-panel-header-info">
              <span class="cs-panel-sparkle"></span>
              <span class="cs-panel-title">优淘智能客服</span>
            </div>
            <div class="cs-panel-header-actions">
              <button class="cs-panel-icon-btn" @click="startNewConversation" title="新对话">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>
              </button>
              <button class="cs-panel-icon-btn" @click="toggleDark" :title="dark ? '切换为浅色' : '切换为深色'">
                <svg v-if="!dark" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
              </button>
              <button class="cs-panel-icon-btn" @click="toggle" title="关闭">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
          </div>
          <div class="cs-messages" ref="messagesContainer">
            <div v-for="(m, i) in messages" :key="i" :class="['cs-message', m.role === 'user' ? 'cs-message-self' : 'cs-message-ai']">
              <!-- Avatar — only render for AI; user side is right-aligned so the avatar is omitted to match the Kiki layout. -->
              <img v-if="m.role === 'ai' && !avatarError" :src="avatarUrl" class="cs-msg-avatar" alt="AI" @error="avatarError = true" />
              <div v-else-if="m.role === 'ai'" class="cs-msg-avatar cs-msg-avatar-fallback">AI</div>

              <div class="cs-msg-content">
                <!-- ============================================================
                     User message bubble (right-aligned). Renders plain text
                     plus any attached file chips so the conversation
                     history reads correctly. The avatar is intentionally
                     omitted (Kiki does the same — user is right-aligned
                     and self-identifying).
                     ============================================================ -->
                <template v-if="m.role === 'user'">
                  <div v-if="m.attachments && m.attachments.length" class="cs-msg-attachments">
                    <div v-for="a in m.attachments" :key="a.id" class="cs-msg-attachment">
                      <span v-if="a.preview" class="cs-msg-attachment-thumb">
                        <img :src="a.preview" :alt="a.name" />
                      </span>
                      <span v-else class="cs-msg-attachment-icon"></span>
                      <span class="cs-msg-attachment-name">{{ a.name }}</span>
                    </div>
                  </div>
                  <div v-if="m.text" class="cs-message-bubble cs-message-self-bubble">{{ m.text }}</div>
                </template>

                <!-- ============================================================
                     Welcome card (Kiki-style): big greeting + subtitle + a stack
                     of clickable suggestion chips. Renders only for the very
                     first AI message of the conversation. Layout is a
                     full-width knowledge card with extra padding.
                     ============================================================ -->
                <div v-else-if="m.kind === 'welcome'" class="cs-knowledge-card cs-welcome">
                  <div class="cs-welcome-title">{{ m.title }}</div>
                  <div class="cs-welcome-subtitle">{{ m.subtitle }}</div>
                  <div class="cs-suggestions">
                    <button
                      v-for="s in m.suggestions"
                      :key="s.id"
                      class="cs-suggestion-chip"
                      :disabled="sending"
                      @click="applySuggestion(s)"
                    >
                      <span class="cs-suggestion-bullet"></span>
                      <span class="cs-suggestion-label">{{ s.label }}</span>
                      <span class="cs-suggestion-arrow">›</span>
                    </button>
                  </div>
                </div>

                <!-- ============================================================
                     AI answer card: knowledge-card style with markdown.
                     Reasoning (if any) is shown as a thin "preparing" mini
                     card below the answer card (Kiki style), with the
                     step-by-step list tucked behind a chevron toggle.
                     ============================================================ -->
                <template v-else>
                  <!-- Thinking mini-card (Kiki-style "正在为操作做准备").
                       Shown while the AI is typing. Collapsed by default
                       once the answer arrives — the user can click the
                       chevron to inspect the step list. -->
                  <div
                    v-if="m.typing && m.reasoning && m.reasoning.length === 0"
                    class="cs-thinking-card"
                  >
                    <span class="cs-thinking-spinner"></span>
                    <span class="cs-thinking-text">正在为操作做准备</span>
                  </div>

                  <!-- Reasoning toggle strip (Kiki-style, collapsed by
                       default once the answer is in). -->
                  <div
                    v-if="m.reasoning && m.reasoning.length > 0"
                    class="cs-reasoning-toggle-strip"
                    @click="toggleReasoning(m)"
                  >
                    <span class="cs-reasoning-toggle-icon" :class="{ 'cs-spinner-sm': m.typing }">
                      <span v-if="!m.typing">●</span>
                    </span>
                    <span class="cs-reasoning-toggle-label">
                      {{ m.typing ? '正在为操作做准备' : '已查看思考过程' }}
                    </span>
                    <span v-if="m.reasoning && m.reasoning.length" class="cs-reasoning-toggle-meta">
                      {{ m.reasoning.length }} 步
                      <span v-if="m.summary && m.summary.total_latency_ms" class="cs-reasoning-latency">
                        · {{ (m.summary.total_latency_ms / 1000).toFixed(1) }}s
                      </span>
                    </span>
                    <span class="cs-reasoning-chevron">{{ m.reasoningExpanded ? '▾' : '▸' }}</span>
                  </div>

                  <!-- Expanded reasoning body -->
                  <div v-if="m.reasoningExpanded && m.reasoning && m.reasoning.length" class="cs-reasoning-body-inline">
                    <div v-for="(s, si) in m.reasoning" :key="s.step_id || si" class="cs-reasoning-step" :class="'cs-reasoning-step-' + s.status">
                      <span class="cs-reasoning-step-icon">
                        <span v-if="s.status === 'running'" class="cs-spinner"></span>
                        <span v-else class="cs-check-glyph"></span>
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

                  <!-- Kiki multi-turn: interim answer bubbles (narration
                       emitted before tool calls). Shown between the reasoning
                       card and the final answer. -->
                  <div
                    v-if="m.interimAnswers && m.interimAnswers.length"
                    class="cs-interim-answers"
                  >
                    <div
                      v-for="(ia, iai) in m.interimAnswers"
                      :key="'interim-' + iai"
                      class="cs-knowledge-card cs-interim-answer"
                      v-html="renderMarkdown(ia)"
                    ></div>
                  </div>

                  <!-- The actual answer — white knowledge card. -->
                  <div v-if="m.text" class="cs-knowledge-card" v-html="renderMarkdown(m.text)"></div>

                  <!-- Kiki-style: primary gradient action button ("帮我操作"). -->
                  <button
                    v-if="!m.typing && m.primaryAction"
                    class="cs-primary-action"
                    :disabled="sending"
                    @click="runPrimaryAction(m.primaryAction)"
                  >
                    <span :class="['cs-primary-action-icon', 'cs-primary-action-icon-' + m.primaryAction.icon]"></span>
                    <span class="cs-primary-action-label">{{ m.primaryAction.label }}</span>
                  </button>

                  <!-- Kiki action cards: clickable buttons from [ACTION] blocks. -->
                  <div
                    v-if="!m.typing && m.actionCards && m.actionCards.length"
                    class="cs-action-cards"
                  >
                    <button
                      v-for="card in m.actionCards"
                      :key="card.id"
                      class="cs-action-card-btn"
                      :disabled="sending"
                      @click="sendMessage(card.prompt)"
                    >
                      <span class="cs-action-card-sparkle">✦</span>
                      <span class="cs-action-card-label">{{ card.label }}</span>
                    </button>
                  </div>

                  <!-- Kiki-style: follow-up suggestion chips ("你可以继续："). -->
                  <div v-if="!m.typing && m.followups && m.followups.length" class="cs-followups">
                    <div class="cs-followups-title">你可以继续：</div>
                    <div class="cs-followups-list">
                      <button
                        v-for="fu in m.followups"
                        :key="fu.id"
                        class="cs-followup-chip"
                        :disabled="sending"
                        @click="runFollowUp(fu)"
                      >
                        <span class="cs-followup-bullet"></span>
                        <span class="cs-followup-label">{{ fu.label }}</span>
                        <span class="cs-followup-arrow">›</span>
                      </button>
                    </div>
                  </div>

                  <!-- Per-message feedback (thumb up/down) -->
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
                </template>
              </div>
            </div>
          </div>
          <!-- Bottom input area — Kiki-style. A subtle grey surface
               holds the attachments strip, the floating input box, and
               the disclaimer. The input box itself is white with a
               shadow, so it visibly "floats" above this surface
               instead of blending into a solid white block. -->
          <div class="cs-input-area">
          <!-- Attachment preview strip — sits above the input box, only
               rendered when there are pending files. -->
          <div v-if="attachments.length" class="cs-attachments">
            <div v-for="a in attachments" :key="a.id" class="cs-attachment-chip">
              <span v-if="a.preview" class="cs-attachment-thumb">
                <img :src="a.preview" :alt="a.name" />
              </span>
              <span v-else class="cs-attachment-file-icon"></span>
              <span class="cs-attachment-meta">
                <span class="cs-attachment-name">{{ a.name }}</span>
                <span class="cs-attachment-size">{{ fmtSize(a.size) }}</span>
              </span>
              <button class="cs-attachment-remove" @click="removeAttachment(a.id)" title="移除">×</button>
            </div>
          </div>

          <!-- Single rounded container holding the textarea (top) and
               the tools row (bottom). Kiki-style layout: instead of
               two pill bars stacked, the input lives in one big
               rounded box with the action row nested inside. -->
          <div
            class="cs-input-box"
            ref="inputBar"
            @dragover="onInputDragOver"
            @drop="onInputDrop"
            :class="{ 'cs-input-box-drag': false }"
          >
            <textarea
              v-model="input"
              class="cs-textarea"
              placeholder="有什么关于订单、商品的问题，都可以问我"
              rows="1"
              @keydown.enter.exact.prevent="send"
              :disabled="sending"
              @input="autoResize"
            ></textarea>
            <div class="cs-input-tools-row">
              <button class="cs-tool-btn" :title="'附件（最多 5MB/个）'" @click="pickFiles">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              </button>
              <div class="cs-mode-chip-wrap">
                <button
                  class="cs-mode-chip"
                  :class="{ 'cs-mode-chip-open': modeMenuOpen }"
                  @click="toggleModeMenu"
                  :title="currentMode.hint"
                >
                  <span :class="['cs-mode-icon', 'cs-mode-icon-' + currentMode.icon]"></span>
                  <span>{{ currentMode.label }}</span>
                  <span class="cs-mode-caret">▾</span>
                </button>
                <div v-if="modeMenuOpen" class="cs-mode-menu" @click.stop>
                  <button
                    v-for="m in MODES"
                    :key="m.id"
                    :class="['cs-mode-menu-item', mode === m.id ? 'active' : '']"
                    @click="selectMode(m.id)"
                  >
                    <span :class="['cs-mode-icon', 'cs-mode-icon-' + m.icon]"></span>
                    <span class="cs-mode-menu-label">{{ m.label }}</span>
                    <span class="cs-mode-menu-hint">{{ m.hint }}</span>
                  </button>
                </div>
              </div>
              <div class="cs-input-tools-spacer"></div>
              <button class="cs-send-btn-circle" @click="send" :disabled="sending || (!input.trim() && !attachments.length)" title="发送">
                <svg v-if="!sending" width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                <span v-else class="cs-send-btn-circle-spinner"></span>
              </button>
            </div>
            <input
              ref="fileInput"
              type="file"
              :accept="ACCEPT"
              multiple
              style="display: none;"
              @change="onFilesPicked"
            />
          </div>
          <div class="cs-disclaimer">推荐和回答由 AI 生成，仅供参考</div>
          </div>
        </div>

        <!-- Kiki-style: vertical side button column. Renders only when
             the panel is open. Sits to the right of the panel and stays
             inside the same vertical strip as the launcher. -->
        <div v-if="open" class="cs-side-rail">
          <button class="cs-side-rail-btn cs-side-rail-btn-sale" title="联系销售">
            <span class="cs-side-rail-vertical">联系销售</span>
          </button>
          <button class="cs-side-rail-btn" title="人工客服">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
          </button>
          <button class="cs-side-rail-btn" title="聊天">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          </button>
          <button class="cs-side-rail-btn cs-side-rail-btn-ai" title="AI 焕新">
            <span class="cs-side-rail-ai-mark">AI</span>
          </button>
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

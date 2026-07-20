/* API client — wraps fetch with consistent error handling, base URL, headers.
 *
 * All calls return parsed JSON. Throws Error on non-2xx with the server's
 * `detail` field (FastAPI convention) as the message.
 */
(function (global) {
  const API_BASE = '/api';

  function getUserId() {
    let uid = localStorage.getItem('0719_shop_user_id');
    if (!uid) {
      uid = 'user-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
      localStorage.setItem('0719_shop_user_id', uid);
    }
    return uid;
  }

  function getSessionId() {
    // Used as customer-service thread_id so conversations persist across reloads.
    let sid = sessionStorage.getItem('0719_shop_session');
    if (!sid) {
      sid = 'cs-' + Math.random().toString(36).slice(2, 10);
      sessionStorage.setItem('0719_shop_session', sid);
    }
    return sid;
  }

  // ----- Internal: SSE parsing & streaming ------------------------
  // parseSseBlock — turn one SSE event block (potentially multi-line)
  // into { event, data }. Tolerates both LF and CRLF line endings and
  // unknown event names default to 'message'.
  function parseSseBlock(block) {
    let event = 'message';
    const dataLines = [];
    const lines = block.split('\n');
    for (const raw of lines) {
      const line = raw.replace(/\r$/, '');
      if (!line || line.startsWith(':')) continue;
      const idx = line.indexOf(':');
      const field = idx < 0 ? line : line.slice(0, idx);
      let val = idx < 0 ? '' : line.slice(idx + 1);
      if (val.startsWith(' ')) val = val.slice(1);
      if (field === 'event') event = val || 'message';
      else if (field === 'data') dataLines.push(val);
    }
    if (dataLines.length === 0) return null;
    const dataStr = dataLines.join('\n');
    let data = dataStr;
    try { data = JSON.parse(dataStr); } catch (_) { /* keep raw string */ }
    return { event, data };
  }
  // streamAgentChat — shared POST+SSE driver used by both the JSON
  // and the multipart variants. The same CRLF-normalizing SSE
  // parsing is reused so both code paths behave identically.
  function streamAgentChat({ url, headers, body, onEvent }) {
    const controller = new AbortController();
    (async () => {
      let resp;
      try {
        resp = await fetch(url, { method: 'POST', headers, body, signal: controller.signal });
      } catch (e) {
        if (e.name === 'AbortError') return;
        onEvent('error', { message: 'network error: ' + e.message, trace_id: null });
        return;
      }
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json()).detail || detail; } catch (_) { /* ignore */ }
        onEvent('error', { message: detail, trace_id: null });
        return;
      }
      if (!resp.body) {
        onEvent('error', { message: 'no response body', trace_id: null });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          // Normalize CRLF -> LF (sse-starlette emits CRLF, and the SSE
          // spec uses blank line as event separator). Without this, a
          // server emitting "\r\n\r\n" would never match "\n\n" and all
          // events would pile up in buf unprocessed — manifesting as
          // "stuck thinking" in the UI.
          buf = buf.replace(/\r\n/g, '\n');
          let sep;
          while ((sep = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, sep);
            buf = buf.slice(sep + 2);
            const parsed = parseSseBlock(block);
            if (parsed) onEvent(parsed.event, parsed.data);
          }
        }
        if (buf.trim()) {
          const parsed = parseSseBlock(buf);
          if (parsed) onEvent(parsed.event, parsed.data);
        }
      } catch (e) {
        if (e.name === 'AbortError') return;
        onEvent('error', { message: 'stream read error: ' + e.message, trace_id: null });
      }
    })();
    return controller;
  }

  async function request(path, options = {}) {
    const url = API_BASE + path;
    const headers = {
      'Content-Type': 'application/json',
      'X-User-Id': getUserId(),
      ...(options.headers || {}),
    };
    const resp = await fetch(url, {
      ...options,
      headers,
    });
    if (resp.status === 204) return null;
    let data;
    try {
      data = await resp.json();
    } catch (e) {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return null;
    }
    if (!resp.ok) {
      const msg = data.detail || data.message || `HTTP ${resp.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  /**
   * Parse one SSE block (e.g. "event: step_start\ndata: {...}") into
   * {event, data}. Returns null if the block has no data line.
   *
   * Per the SSE spec, multiple `data:` lines are concatenated with \n.
   * We parse the JSON once at the end; if parsing fails we return the
   * raw string so the caller can still see what came through.
   */
  function parseSseBlock(block) {
    const lines = block.split('\n');
    let event = 'message';
    let dataStr = '';
    for (const line of lines) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataStr += (dataStr ? '\n' : '') + line.slice(5).trim();
      }
    }
    if (!dataStr) return null;
    let data;
    try { data = JSON.parse(dataStr); }
    catch (_) { data = { raw: dataStr }; }
    return { event, data };
  }

  const api = {
    getUserId,
    getSessionId,

    // Catalog
    getCategories: () => request('/catalog/categories'),
    listProducts: (params = {}) => {
      const q = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') q.append(k, v);
      });
      return request('/catalog/products?' + q.toString());
    },
    getHotProducts: (limit = 10) => request('/catalog/products/hot?limit=' + limit),
    getNewProducts: (limit = 10) => request('/catalog/products/new?limit=' + limit),
    getProduct: (id) => request(`/catalog/products/${id}`),
    getRelated: (id, limit = 6) => request(`/catalog/products/${id}/related?limit=${limit}`),

    // Cart
    getCart: () => request('/cart'),
    addCartItem: (sku_id, quantity = 1, selected = true) =>
      request('/cart/items', { method: 'POST', body: JSON.stringify({ sku_id, quantity, selected }) }),
    updateCartItem: (id, updates) =>
      request(`/cart/items/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
    removeCartItem: (id) => request(`/cart/items/${id}`, { method: 'DELETE' }),
    clearCart: () => request('/cart', { method: 'DELETE' }),

    // Users & addresses
    ensureUser: (user_id, nickname, avatar) =>
      request('/users/ensure', { method: 'POST', body: JSON.stringify({ user_id, nickname, avatar }) }),
    getMe: () => request('/users/me'),
    listAddresses: () => request('/users/addresses'),
    addAddress: (data) => request('/users/addresses', { method: 'POST', body: JSON.stringify(data) }),
    updateAddress: (id, data) => request(`/users/addresses/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteAddress: (id) => request(`/users/addresses/${id}`, { method: 'DELETE' }),

    // Orders
    createOrder: (data) => request('/orders', { method: 'POST', body: JSON.stringify(data) }),
    listOrders: (params = {}) => {
      const q = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') q.append(k, v);
      });
      return request('/orders?' + q.toString());
    },
    getOrder: (id) => request(`/orders/${id}`),
    cancelOrder: (id, reason) =>
      request(`/orders/${id}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) }),
    payOrder: (id, method = 'alipay') =>
      request(`/orders/${id}/payment`, { method: 'POST', body: JSON.stringify({ order_id: id, method }) }),
    addReview: (order_id, product_id, rating, content) =>
      request(`/orders/${order_id}/reviews`, {
        method: 'POST',
        body: JSON.stringify({ product_id, rating, content }),
      }),

    // Recommendations
    recommendForUser: (limit = 10) => request(`/recommendations/for-user?limit=${limit}`),
    recommendHot: (limit = 10) => request(`/recommendations/hot?limit=${limit}`),
    recommendNew: (limit = 10) => request(`/recommendations/new?limit=${limit}`),
    recommendRelated: (product_id, limit = 6) =>
      request(`/recommendations/related/${product_id}?limit=${limit}`),

    // Customer service
    chatWithAgent: (message, thread_id, context) =>
      request('/customer-service/chat', {
        method: 'POST',
        body: JSON.stringify({ message, thread_id, context }),
      }),
    /**
     * Stream a customer-service chat turn via SSE.
     *
     * Calls onEvent(eventType, data) for each upstream SSE event:
     *   meta | route | step_start | step_end | token | final | summary | error
     *
     * Returns an AbortController so the caller can cancel an in-flight
     * stream (e.g. when the user closes the chat panel).
     *
     * NOTE: We deliberately use fetch + getReader() instead of EventSource
     * because EventSource only supports GET and the agent's /stream endpoint
     * is POST (it needs a JSON body with the user message).
     */
    /**
     * Streams a customer-service agent response via Server-Sent Events.
     *
     * Internally shared with chatWithAgentStreamAttachments so the
     * multipart (with-files) variant can reuse the same SSE parsing.
     * Returns an AbortController so the caller can cancel an in-flight
     * stream (e.g. when the user closes the chat panel).
     *
     * NOTE: We deliberately use fetch + getReader() instead of EventSource
     * because EventSource only supports GET and the agent's /stream endpoint
     * is POST (it needs a JSON body with the user message).
     */
    chatWithAgentStream: (message, thread_id, context, onEvent) => {
      const body = JSON.stringify({ message, thread_id, context });
      return streamAgentChat({
        url: API_BASE + '/customer-service/chat/stream',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Id': getUserId(),
          'Accept': 'text/event-stream',
        },
        body,
        onEvent,
      });
    },
    /**
     * Variant of chatWithAgentStream that uploads files alongside the
     * prompt. Builds a multipart/form-data body:
     *   - message: the user prompt
     *   - thread_id: conversation thread id
     *   - context: serialized JSON (mode, current_page, etc.)
     *   - files: one form entry per File object
     *
     * The backend /customer-service/chat/stream endpoint is expected to
     * accept the same multipart body and ingest files (forwarded to the
     * agent as inline references). If the backend returns 404 / 405
     * (older builds), the caller should fall back to the JSON variant.
     */
    chatWithAgentStreamAttachments: (message, thread_id, context, files, onEvent) => {
      const fd = new FormData();
      fd.append('message', message || '');
      fd.append('thread_id', thread_id || '');
      fd.append('context', JSON.stringify(context || {}));
      for (let i = 0; i < files.length; i++) {
        const att = files[i];
        // `att` is the wrapper {id, name, size, type, file, preview} the
        // widget stores. FormData.append() needs the actual File (which
        // IS a Blob) — passing the wrapper object makes the browser throw
        // "parameter 2 is not of type 'Blob'". We support both shapes so
        // legacy callers that pass raw Files keep working.
        const fileObj = (att && att.file) ? att.file : att;
        const fileName = (att && att.name) ? att.name : (fileObj && fileObj.name) || 'file';
        // Use the field name "files" (matches FastAPI's File parameter
        // when declared as `files: List[UploadFile] = File(...)`).
        fd.append('files', fileObj, fileName);
      }
      return streamAgentChat({
        url: API_BASE + '/customer-service/chat/stream',
        headers: {
          // Do NOT set Content-Type — the browser sets it with the
          // correct multipart boundary.
          'X-User-Id': getUserId(),
          'Accept': 'text/event-stream',
        },
        body: fd,
        onEvent,
      });
    },
    checkAgentHealth: () => request('/customer-service/health'),
    sendAgentFeedback: (trace_id, feedback) =>
      request('/customer-service/feedback', {
        method: 'POST',
        body: JSON.stringify({ trace_id, feedback }),
      }),

    // Health
    health: () => request('/health'),
  };

  global.ShopAPI = api;
})(window);

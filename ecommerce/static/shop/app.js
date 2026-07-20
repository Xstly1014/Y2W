/* App entry — wires Vue + Vue Router + Pinia together, registers global
 * components, defines routes, mounts to #app.
 */
(function (global) {
  const { createApp, h, defineComponent, onMounted } = Vue;
  const { createRouter, createWebHistory } = VueRouter;
  const { createPinia } = Pinia;

  // Layout wrapper that renders header + category bar + page content + footer.
  const AppLayout = defineComponent({
    name: 'AppLayout',
    setup() {
      const cartStore = global.ShopStores.useCartStore();
      const userStore = global.ShopStores.useUserStore();
      const catStore = global.ShopStores.useCategoryStore();
      onMounted(async () => {
        catStore.load();
        await userStore.init();
        await cartStore.load();
      });
      return { cartStore };
    },
    render() {
      return h('div', [
        h(global.AppHeader),
        h(global.CategoryBar),
        h('main', { class: 'main-content' }, [h(VueRouter.RouterView)]),
        h(global.AppFooter),
        h(global.ToastContainer),
        h(global.CustomerServiceWidget),
      ]);
    },
  });

  const routes = [
    { path: '/', component: global.HomePage },
    { path: '/search', component: global.ProductListPage },
    { path: '/category/:id', component: global.ProductListPage },
    { path: '/product/:id', component: global.ProductDetailPage },
    { path: '/cart', component: global.CartPage },
    { path: '/checkout', component: global.CheckoutPage },
    { path: '/pay/:id', component: global.PayPage },
    { path: '/pay-success/:id', component: global.PaySuccessPage },
    { path: '/orders', component: global.OrderListPage },
    { path: '/order/:id', component: global.OrderDetailPage },
    { path: '/user', component: global.UserCenterPage },
    { path: '/user/addresses', component: global.AddressManagePage },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ];

  const router = createRouter({
    history: createWebHistory(),
    routes,
    scrollBehavior(to, from, saved) {
      if (saved) return saved;
      return { top: 0 };
    },
  });

  const app = createApp({
    render: () => h(AppLayout),
  });
  app.use(createPinia());
  app.use(router);
  // Register the customer-service widget globally (it lives outside the
  // layout so it floats over every page).
  app.component('CustomerServiceWidget', global.CustomerServiceWidget);
  app.mount('#app');

  global.ShopApp = app;
})(window);

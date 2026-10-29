"use strict";
/* Витрина магазина — Telegram Mini App на чистом JS, без сборки.
   В Telegram используются нативные MainButton / BackButton / HapticFeedback / CloudStorage,
   в обычном браузере — запасные кнопки (режим предпросмотра, без оформления заказа). */

const tg = window.Telegram?.WebApp;
const inTelegram = Boolean(tg?.initData);
// Открыто в Telegram, но без подписи пользователя — например, с кнопки клавиатуры: initData там пустая
const unsignedTelegram = !inTelegram && Boolean(tg?.platform) && tg.platform !== "unknown";
const NEED_SIGNED_OPEN = unsignedTelegram
  ? "Чтобы оформить заказ, откройте магазин кнопкой «Магазин» слева от поля ввода в чате с ботом"
  : "Режим предпросмотра. Чтобы оформить заказ, откройте магазин в Telegram";
const MAX_QTY = 20; // как MAX_QTY_PER_PRODUCT на сервере
const PENDING_REUSE_MS = 20 * 60 * 1000; // сервер отменяет неоплаченный заказ через 30 мин
const HUES = [30, 195, 14, 45, 340, 160]; // тёплые тона под кофейную тематику, по категориям

const state = {
  shop: null,
  categories: [],
  products: [],
  byId: new Map(),
  cart: new Map(), // productId -> qty
  category: "all",
  query: "",
  screen: "catalog",
  sheetId: null,
  delivery: "courier",
  pending: null, // { signature, order, createdAt } — неоплаченный заказ, чтобы не плодить дубли
  busy: false,
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function money(minor) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: state.shop?.currency || "RUB",
    maximumFractionDigits: minor % 100 ? 2 : 0,
  }).format(minor / 100);
}

const haptic = {
  tap: () => inTelegram && tg.HapticFeedback.impactOccurred("light"),
  select: () => inTelegram && tg.HapticFeedback.selectionChanged(),
  ok: () => inTelegram && tg.HapticFeedback.notificationOccurred("success"),
  warn: () => inTelegram && tg.HapticFeedback.notificationOccurred("warning"),
  error: () => inTelegram && tg.HapticFeedback.notificationOccurred("error"),
};

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

// ---------- API ----------

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  // Подписанные Telegram данные пользователя — сервер проверяет подпись и по ней узнаёт покупателя
  if (inTelegram) headers.Authorization = `tma ${tg.initData}`;
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch {
    throw new Error("Нет связи с сервером. Проверьте интернет");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : "Что-то пошло не так. Попробуйте ещё раз");
  }
  return body;
}

// CloudStorage Telegram синхронизирует корзину между устройствами; вне Telegram — localStorage
const useCloud = inTelegram && tg.isVersionAtLeast("6.9");
const store = {
  get(key) {
    if (useCloud) {
      return new Promise((resolve) => tg.CloudStorage.getItem(key, (err, value) => resolve(err ? null : value || null)));
    }
    try {
      return Promise.resolve(localStorage.getItem(key));
    } catch {
      return Promise.resolve(null);
    }
  },
  set(key, value) {
    if (useCloud) return tg.CloudStorage.setItem(key, value);
    try {
      localStorage.setItem(key, value);
    } catch {
      /* приватный режим — корзина просто не переживёт перезагрузку */
    }
  },
};

// ---------- Корзина ----------

const qtyOf = (id) => state.cart.get(id) || 0;
const maxQty = (product) => Math.min(product.stock, MAX_QTY);
const cartLines = () =>
  [...state.cart].map(([id, qty]) => ({ product: state.byId.get(id), qty })).filter((line) => line.product);
const cartCount = () => cartLines().reduce((sum, line) => sum + line.qty, 0);
const itemsTotal = () => cartLines().reduce((sum, line) => sum + line.product.price * line.qty, 0);

function deliveryCost(items = itemsTotal()) {
  if (state.delivery === "pickup" || items >= state.shop.free_delivery_from) return 0;
  return state.shop.delivery_price;
}

function setQty(id, wanted) {
  const product = state.byId.get(id);
  if (!product) return;
  const qty = Math.max(0, Math.min(wanted, maxQty(product)));
  if (wanted > qty) {
    haptic.warn();
    toast(product.stock <= MAX_QTY ? `В наличии только ${product.stock} шт.` : `Не больше ${MAX_QTY} шт. одного товара`);
  } else {
    haptic.tap();
  }
  if (qty) state.cart.set(id, qty);
  else state.cart.delete(id);
  store.set("cart", JSON.stringify([...state.cart]));

  // Точечно обновляем счётчики, не перерисовывая сетку — без мерцания
  for (const el of document.querySelectorAll(`[data-control="${id}"]`)) el.innerHTML = qtyControl(product);
  if (state.screen === "cart") renderCart();
  updateNav();
}

// ---------- Отрисовка ----------

function thumb(product, badge = "") {
  const hue = HUES[(product.category_id - 1) % HUES.length];
  const image = product.image_url
    ? `<img src="${esc(product.image_url)}" alt="" loading="lazy" onerror="this.remove()">`
    : "";
  return `<div class="thumb" style="--hue:${hue}">${esc(product.emoji)}${image}${badge}</div>`;
}

function qtyControl(product) {
  if (product.stock <= 0) return `<button class="btn-add" disabled>Нет в наличии</button>`;
  const qty = qtyOf(product.id);
  if (!qty) return `<button class="btn-add" data-action="inc" data-id="${product.id}">В корзину</button>`;
  return `
    <div class="stepper">
      <button data-action="dec" data-id="${product.id}" aria-label="Убрать одну">−</button>
      <span>${qty}</span>
      <button data-action="inc" data-id="${product.id}" aria-label="Добавить ещё">+</button>
    </div>`;
}

function stockLabel(product) {
  if (product.stock <= 0) return "Нет в наличии";
  if (product.stock <= 5) return `Осталось ${product.stock} шт.`;
  return "В наличии";
}

function renderChips() {
  const chips = [{ id: "all", title: "Все", emoji: "✨" }, ...state.categories];
  $("#chips").innerHTML = chips
    .map(
      (c) =>
        `<button class="chip" data-category="${c.id}" aria-pressed="${state.category === c.id}">${esc(c.emoji)} ${esc(c.title)}</button>`,
    )
    .join("");
}

function visibleProducts() {
  const query = state.query.trim().toLowerCase();
  return state.products.filter(
    (p) =>
      (state.category === "all" || p.category_id === state.category) &&
      (!query || p.title.toLowerCase().includes(query) || p.description.toLowerCase().includes(query)),
  );
}

function renderGrid() {
  const products = visibleProducts();
  $("#grid").innerHTML = products
    .map((p) => {
      const badge = p.stock > 0 && p.stock <= 5 ? `<span class="badge">Осталось ${p.stock}</span>` : "";
      return `
        <article class="product ${p.stock <= 0 ? "sold-out" : ""}" data-open="${p.id}">
          ${thumb(p, badge)}
          <div class="product-body">
            <div class="product-title">${esc(p.title)}</div>
            <div class="product-price">${money(p.price)}</div>
            <div data-control="${p.id}">${qtyControl(p)}</div>
          </div>
        </article>`;
    })
    .join("");
  $("#grid-empty").classList.toggle("hidden", products.length > 0);
}

function renderSkeleton() {
  $("#grid").innerHTML = Array.from(
    { length: 6 },
    () => `
      <div class="product skeleton">
        <div class="thumb"></div>
        <div class="product-body"><div class="line"></div><div class="line" style="width: 50%"></div></div>
      </div>`,
  ).join("");
}

function summaryHtml({ freeDeliveryHint }) {
  const items = itemsTotal();
  const delivery = deliveryCost(items);
  let hint = "";
  if (freeDeliveryHint && state.delivery === "courier" && delivery > 0) {
    const percent = Math.min(100, Math.round((items / state.shop.free_delivery_from) * 100));
    hint = `
      <div class="free-hint">Добавьте ещё на ${money(state.shop.free_delivery_from - items)} — доставка будет бесплатной</div>
      <div class="progress"><div style="width: ${percent}%"></div></div>`;
  }
  return `
    <div class="row"><span>Товары, ${cartCount()} шт.</span><span>${money(items)}</span></div>
    <div class="row"><span>${state.delivery === "pickup" ? "Самовывоз" : "Доставка"}</span><span>${delivery ? money(delivery) : "бесплатно"}</span></div>
    ${hint}
    <div class="row total"><span>Итого</span><span>${money(items + delivery)}</span></div>`;
}

function renderCart() {
  const lines = cartLines();
  $("#cart-list").innerHTML = lines.length
    ? lines
        .map(
          ({ product: p, qty }) => `
            <div class="card cart-row">
              ${thumb(p)}
              <div>
                <div class="title">${esc(p.title)}</div>
                <div class="meta">${money(p.price)} × ${qty} = ${money(p.price * qty)}</div>
              </div>
              <div data-control="${p.id}">${qtyControl(p)}</div>
            </div>`,
        )
        .join("")
    : `<p class="empty">Корзина пуста — добавьте что-нибудь вкусное ☕</p>`;
  $("#cart-summary").innerHTML = lines.length ? summaryHtml({ freeDeliveryHint: true }) : "";
  $("#cart-summary").classList.toggle("hidden", !lines.length);
}

function renderCheckout() {
  const pickup = state.delivery === "pickup";
  $("#address-field").classList.toggle("hidden", pickup);
  $("#pickup-hint").classList.toggle("hidden", !pickup);
  $("#pickup-hint").textContent = `Заберите заказ по адресу: ${state.shop.pickup_address}`;
  $("#checkout-summary").innerHTML = summaryHtml({ freeDeliveryHint: false });
}

// ---------- Карточка товара ----------

function openSheet(id) {
  const p = state.byId.get(id);
  if (!p) return;
  state.sheetId = id;
  $("#sheet").innerHTML = `
    <button class="sheet-close" data-action="close" aria-label="Закрыть">✕</button>
    ${thumb(p)}
    <div class="sheet-body">
      <h2 class="sheet-title">${esc(p.title)}</h2>
      ${p.description ? `<p class="sheet-desc">${esc(p.description)}</p>` : ""}
      <div class="sheet-row">
        <div>
          <div class="sheet-price">${money(p.price)}</div>
          <div class="sheet-stock">${stockLabel(p)}</div>
        </div>
        <div data-control="${p.id}">${qtyControl(p)}</div>
      </div>
    </div>`;
  $("#sheet").classList.remove("hidden");
  $("#sheet-backdrop").classList.remove("hidden");
  updateNav();
}

function closeSheet() {
  state.sheetId = null;
  $("#sheet").classList.add("hidden");
  $("#sheet-backdrop").classList.add("hidden");
  updateNav();
}

// ---------- Навигация и нативные кнопки ----------

const SCREENS = ["catalog", "cart", "checkout", "success", "orders"];
const BACK_TO = { cart: "catalog", checkout: "cart", orders: "catalog", success: "catalog" };

function go(screen) {
  state.screen = screen;
  for (const s of SCREENS) $(`#screen-${s}`).classList.toggle("hidden", s !== screen);
  if (screen === "cart") renderCart();
  if (screen === "checkout") renderCheckout();
  showError(null);
  window.scrollTo({ top: 0 });
  updateNav();
}

function goBack() {
  if (state.sheetId !== null) return closeSheet();
  if (BACK_TO[state.screen]) go(BACK_TO[state.screen]);
}

let mainHandler = null;

function updateNav() {
  if (!state.shop) return;
  const count = cartCount();
  const total = itemsTotal();
  const payable = total + deliveryCost(total);
  let text = null;
  let handler = null;

  if (state.sheetId !== null) {
    if (count) [text, handler] = [`Корзина · ${money(total)}`, () => (closeSheet(), go("cart"))];
  } else if (state.screen === "catalog" && count) {
    [text, handler] = [`Корзина · ${count} шт. · ${money(total)}`, () => go("cart")];
  } else if (state.screen === "cart" && count) {
    [text, handler] = [`Оформить · ${money(payable)}`, () => go("checkout")];
  } else if (state.screen === "checkout") {
    [text, handler] = [`${state.shop.demo_payments ? "Оплатить (демо)" : "Оплатить"} · ${money(payable)}`, submitOrder];
  } else if (state.screen === "success") {
    [text, handler] = ["Вернуться в магазин", () => go("catalog")];
  }

  mainHandler = handler;
  const backVisible = state.sheetId !== null || state.screen !== "catalog";
  if (inTelegram) {
    if (text) tg.MainButton.setParams({ text, is_visible: true, is_active: true });
    else tg.MainButton.hide();
    if (backVisible) tg.BackButton.show();
    else tg.BackButton.hide();
  } else {
    $("#fallback-main").textContent = text || "";
    $("#fallback-main").classList.toggle("hidden", !text);
    $("#fallback-back").classList.toggle("hidden", !backVisible);
    $("#fallback-bar").classList.toggle("hidden", !text && !backVisible);
  }
}

function setBusy(busy) {
  state.busy = busy;
  if (inTelegram) {
    if (busy) tg.MainButton.showProgress(false);
    else tg.MainButton.hideProgress();
  } else {
    $("#fallback-main").disabled = busy;
  }
}

// ---------- Оформление и оплата ----------

function showError(message) {
  $("#checkout-error").textContent = message || "";
  $("#checkout-error").classList.toggle("hidden", !message);
}

function readForm() {
  const fields = $("#checkout-form").elements;
  const value = (name) => fields.namedItem(name).value.trim();
  return {
    delivery: state.delivery,
    name: value("name"),
    phone: value("phone"),
    address: state.delivery === "courier" ? value("address") || null : null,
    comment: value("comment") || null,
  };
}

function validate(data) {
  const fields = $("#checkout-form").elements;
  const checks = [
    ["name", data.name.length >= 2, "Укажите имя получателя"],
    ["phone", /^\+?[\d\s()-]{10,20}$/.test(data.phone), "Проверьте номер телефона"],
    ["address", state.delivery === "pickup" || (data.address || "").length >= 5, "Укажите адрес доставки"],
  ];
  let firstError = null;
  for (const [name, ok, message] of checks) {
    fields.namedItem(name).closest(".field").classList.toggle("invalid", !ok);
    if (!ok && !firstError) firstError = message;
  }
  return firstError;
}

async function submitOrder() {
  if (state.busy) return;
  if (!inTelegram) return toast(NEED_SIGNED_OPEN);
  const data = readForm();
  const error = validate(data);
  showError(error);
  if (error) return haptic.error();

  const body = JSON.stringify({ ...data, items: cartLines().map((l) => ({ product_id: l.product.id, qty: l.qty })) });
  setBusy(true);
  try {
    // Покупатель закрыл окно оплаты и нажал снова — открываем тот же счёт, а не создаём второй заказ
    const reusable =
      state.pending?.signature === body && Date.now() - state.pending.createdAt < PENDING_REUSE_MS;
    const order = reusable ? state.pending.order : await api("/api/orders", { method: "POST", body });
    state.pending = { signature: body, order, createdAt: reusable ? state.pending.createdAt : Date.now() };
    store.set("profile", JSON.stringify({ name: data.name, phone: data.phone, address: data.address || "" }));

    if (order.invoice_link) {
      tg.openInvoice(order.invoice_link, (status) => {
        if (status === "paid") onPaid(order);
        else if (status === "failed") {
          haptic.error();
          showError("Оплата не прошла. Попробуйте ещё раз или другую карту");
        }
      });
    } else {
      await api(`/api/orders/${order.order_id}/demo-pay`, { method: "POST" });
      onPaid(order);
    }
  } catch (e) {
    state.pending = null;
    haptic.error();
    showError(e.message);
  } finally {
    setBusy(false);
  }
}

function onPaid(order) {
  haptic.ok();
  state.pending = null;
  state.cart.clear();
  store.set("cart", "[]");
  $("#success-title").textContent = `Заказ №${order.order_id} оплачен!`;
  go("success");
  loadCatalog().catch(() => {}); // остатки изменились
}

// ---------- Мои заказы ----------

async function openOrders() {
  go("orders");
  const list = $("#orders-list");
  if (!inTelegram) {
    list.innerHTML = `<p class="empty">${esc(NEED_SIGNED_OPEN)}</p>`;
    return;
  }
  list.innerHTML = `<p class="empty">Загружаем…</p>`;
  try {
    const orders = await api("/api/orders");
    list.innerHTML = orders.length
      ? orders
          .map(
            (o) => `
              <div class="card order-card">
                <div class="head"><span>Заказ №${o.id}</span><span>${money(o.total)}</span></div>
                <div>${esc(o.status_label)}</div>
                <div class="meta">${esc(o.created_at)} · ${o.items.map((i) => `${esc(i.title)} × ${i.qty}`).join(", ")}</div>
              </div>`,
          )
          .join("")
      : `<p class="empty">Заказов пока нет</p>`;
  } catch (e) {
    list.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}

// ---------- Загрузка ----------

async function loadCatalog() {
  const data = await api("/api/catalog");
  state.shop = data.shop;
  state.categories = data.categories;
  state.products = data.products;
  state.byId = new Map(data.products.map((p) => [p.id, p]));
  // В корзине не может лежать больше, чем осталось на складе
  for (const [id, qty] of state.cart) {
    const product = state.byId.get(id);
    if (!product || product.stock <= 0) state.cart.delete(id);
    else if (qty > maxQty(product)) state.cart.set(id, maxQty(product));
  }

  document.title = data.shop.name;
  $("#shop-name").textContent = data.shop.name;
  $("#shop-sub").textContent = `Доставка ${money(data.shop.delivery_price)} · бесплатно от ${money(data.shop.free_delivery_from)}`;
  renderChips();
  renderGrid();
  if (state.screen === "cart") renderCart();
  updateNav();
}

function prefillCheckout(rawProfile) {
  let profile = {};
  try {
    profile = JSON.parse(rawProfile || "{}");
  } catch {
    /* повреждённые данные — просто не подставляем */
  }
  const user = tg?.initDataUnsafe?.user;
  const fields = $("#checkout-form").elements;
  fields.namedItem("name").value = profile.name || [user?.first_name, user?.last_name].filter(Boolean).join(" ");
  fields.namedItem("phone").value = profile.phone || "";
  fields.namedItem("address").value = profile.address || "";
}

function bindEvents() {
  document.addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]");
    if (action) {
      const id = Number(action.dataset.id);
      if (action.dataset.action === "inc") setQty(id, qtyOf(id) + 1);
      if (action.dataset.action === "dec") setQty(id, qtyOf(id) - 1);
      if (action.dataset.action === "close") closeSheet();
      return;
    }
    const chip = event.target.closest("[data-category]");
    if (chip) {
      haptic.select();
      state.category = chip.dataset.category === "all" ? "all" : Number(chip.dataset.category);
      renderChips();
      renderGrid();
      return;
    }
    const card = event.target.closest("[data-open]");
    if (card) openSheet(Number(card.dataset.open));
  });

  $("#sheet-backdrop").addEventListener("click", closeSheet);
  $("#search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderGrid();
  });
  $("#orders-btn").addEventListener("click", openOrders);
  $("#checkout-form").addEventListener("change", (event) => {
    if (event.target.name !== "delivery") return;
    state.delivery = event.target.value;
    haptic.select();
    renderCheckout();
    updateNav();
  });

  if (inTelegram) {
    tg.MainButton.onClick(() => mainHandler?.());
    tg.BackButton.onClick(goBack);
  } else {
    $("#fallback-main").addEventListener("click", () => mainHandler?.());
    $("#fallback-back").addEventListener("click", goBack);
  }
}

async function init() {
  if (inTelegram) {
    tg.ready();
    tg.expand();
    if (tg.isVersionAtLeast("6.1")) {
      tg.setHeaderColor("secondary_bg_color");
      tg.setBackgroundColor("secondary_bg_color");
    }
    // Чтобы прокрутка каталога вниз не сворачивала приложение
    if (tg.isVersionAtLeast("7.7")) tg.disableVerticalSwipes();
  }
  $("#preview-banner").textContent = NEED_SIGNED_OPEN;
  $("#preview-banner").classList.toggle("hidden", inTelegram);
  bindEvents();
  renderSkeleton();

  const [savedCart, savedProfile] = await Promise.all([store.get("cart"), store.get("profile")]);
  try {
    for (const [id, qty] of JSON.parse(savedCart || "[]")) state.cart.set(Number(id), Number(qty));
  } catch {
    state.cart.clear();
  }
  prefillCheckout(savedProfile);

  try {
    await loadCatalog();
  } catch (e) {
    $("#grid").innerHTML = "";
    $("#grid-empty").textContent = e.message;
    $("#grid-empty").classList.remove("hidden");
    $("#shop-sub").textContent = "Не удалось загрузить каталог";
  }
}

init();

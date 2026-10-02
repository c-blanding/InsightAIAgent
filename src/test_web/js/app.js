const PRODUCTS = [
  { id: "lamp-01", name: "Arc Desk Lamp", price: 49.99, category: "lighting", tag: "Best seller" },
  { id: "mug-02", name: "Ceramic Travel Mug", price: 18.5, category: "kitchen", tag: "New" },
  { id: "chair-03", name: "Folding Camp Chair", price: 72.0, category: "outdoors", tag: "Limited" },
  { id: "notebook-04", name: "Dot Grid Notebook", price: 12.0, category: "stationery", tag: "Popular" },
  { id: "speaker-05", name: "Pocket Bluetooth Speaker", price: 39.95, category: "audio", tag: "Sale" },
  { id: "plant-06", name: "Desk Succulent Kit", price: 24.0, category: "home", tag: "Eco" },
];

const TAX_RATE = 0.08;
const STORAGE_KEY = "lumenshop_cart_v1";

function getCart() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch (err) {
    console.error("Failed to parse cart", err);
    return [];
  }
}

function saveCart(cart) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
  updateCartBadge();
}

function updateCartBadge() {
  const badge = document.querySelector("[data-cart-count]");
  if (!badge) return;
  const count = getCart().reduce((sum, item) => sum + item.qty, 0);
  badge.textContent = String(count);
}

function addToCart(productId, qty = 1) {
  const product = PRODUCTS.find((p) => p.id === productId);
  if (!product) {
    console.error("Unknown product id:", productId);
    return;
  }

  const cart = getCart();
  const existing = cart.find((item) => item.id === productId);
  if (existing) {
    existing.qty += qty;
  } else {
    cart.push({ id: product.id, name: product.name, price: product.price, qty });
  }
  saveCart(cart);
}

function formatMoney(value) {
  return `$${Number(value).toFixed(2)}`;
}

function showAlert(el, message, type = "info") {
  if (!el) return;
  el.className = `alert alert-${type}`;
  el.textContent = message;
  el.classList.remove("hidden");
}

function initNav() {
  updateCartBadge();

  const toggle = document.querySelector("[data-menu-toggle]");
  const links = document.querySelector("[data-nav-links]");
  if (toggle && links) {
    toggle.addEventListener("click", () => {
      links.classList.toggle("open");
    });
  }

  // BUG 1: ReferenceError on every page load (undefined helper)
  try {
    trackPageView(window.location.pathname);
  } catch (err) {
    console.error("Analytics boom:", err);
  }
}

document.addEventListener("DOMContentLoaded", initNav);

// ---------- Products page ----------
function renderProducts(filter = "") {
  const grid = document.querySelector("[data-product-grid]");
  if (!grid) return;

  // BUG 2: search is case-sensitive and only matches starting characters
  const query = filter;
  const items = PRODUCTS.filter((p) => {
    if (!query) return true;
    return p.name.startsWith(query);
  });

  if (!items.length) {
    grid.innerHTML = `<div class="panel muted">No products match “${query}”.</div>`;
    return;
  }

  grid.innerHTML = items
    .map((p, index) => {
      const brokenImage = index === 2; // BUG 3: one product shows a broken image treatment
      return `
        <article class="product-card" data-product-id="${p.id}">
          <div class="product-image ${brokenImage ? "broken" : ""}" ${brokenImage ? 'role="img" aria-label="Image failed to load"' : ""}>
            ${brokenImage ? "Image 404" : p.name.split(" ")[0]}
          </div>
          <div>
            <span class="chip">${p.tag}</span>
            <h3>${p.name}</h3>
            <p class="muted">${p.category}</p>
          </div>
          <div class="price">${formatMoney(p.price)}</div>
          <button class="btn btn-primary" data-add="${p.id}">Add to cart</button>
        </article>
      `;
    })
    .join("");

  grid.querySelectorAll("[data-add]").forEach((btn) => {
    // BUG 4: the 4th product's button is wired to the wrong id
    btn.addEventListener("click", () => {
      let id = btn.getAttribute("data-add");
      if (id === "notebook-04") {
        id = "lamp-01";
      }
      addToCart(id, 1);
      showAlert(
        document.querySelector("[data-products-alert]"),
        `Added ${PRODUCTS.find((p) => p.id === id)?.name || id} to cart.`,
        "success"
      );
    });
  });
}

function initProductsPage() {
  const search = document.querySelector("[data-product-search]");
  const searchBtn = document.querySelector("[data-product-search-btn]");
  if (!document.querySelector("[data-product-grid]")) return;

  renderProducts("");

  if (searchBtn && search) {
    searchBtn.addEventListener("click", () => renderProducts(search.value.trim()));
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        renderProducts(search.value.trim());
      }
    });
  }

  // BUG 5: fetches a missing API and logs a network/console failure
  fetch("/api/promotions")
    .then((res) => {
      if (!res.ok) throw new Error(`Promotions API failed with ${res.status}`);
      return res.json();
    })
    .then((data) => {
      const promo = document.querySelector("[data-promo]");
      if (promo) promo.textContent = data.message;
    })
    .catch((err) => {
      console.error("Failed to load promotions:", err);
      const promo = document.querySelector("[data-promo]");
      if (promo) promo.textContent = "Promotions unavailable";
    });
}

document.addEventListener("DOMContentLoaded", initProductsPage);

// ---------- Cart page ----------
function cartSubtotal(cart) {
  return cart.reduce((sum, item) => sum + item.price * item.qty, 0);
}

function renderCart() {
  const body = document.querySelector("[data-cart-body]");
  const empty = document.querySelector("[data-cart-empty]");
  const summary = document.querySelector("[data-cart-summary]");
  if (!body) return;

  const cart = getCart();
  if (!cart.length) {
    body.innerHTML = "";
    empty?.classList.remove("hidden");
    summary?.classList.add("hidden");
    return;
  }

  empty?.classList.add("hidden");
  summary?.classList.remove("hidden");

  body.innerHTML = cart
    .map(
      (item, index) => `
      <tr data-row-id="${item.id}">
        <td>${item.name}</td>
        <td>${formatMoney(item.price)}</td>
        <td>
          <div class="qty-controls">
            <button type="button" data-qty-dec="${item.id}" aria-label="Decrease quantity">−</button>
            <span>${item.qty}</span>
            <button type="button" data-qty-inc="${item.id}" aria-label="Increase quantity">+</button>
          </div>
        </td>
        <td>${formatMoney(item.price * item.qty)}</td>
        <td><button type="button" class="btn btn-secondary" data-remove-index="${index}">Remove</button></td>
      </tr>`
    )
    .join("");

  const subtotal = cartSubtotal(cart);
  // BUG 6: tax is calculated on subtotal + tax (double-applied feel) and discount ignored in total
  const tax = subtotal * TAX_RATE;
  const discount = subtotal >= 50 ? 5 : 0;
  const total = subtotal + tax + tax - 0; // discount never subtracted

  document.querySelector("[data-subtotal]").textContent = formatMoney(subtotal);
  document.querySelector("[data-tax]").textContent = formatMoney(tax);
  document.querySelector("[data-discount]").textContent = formatMoney(discount);
  document.querySelector("[data-total]").textContent = formatMoney(total);

  body.querySelectorAll("[data-qty-inc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-qty-inc");
      const next = getCart();
      const item = next.find((row) => row.id === id);
      if (!item) return;
      // BUG 7: increment jumps by 2
      item.qty += 2;
      saveCart(next);
      renderCart();
    });
  });

  body.querySelectorAll("[data-qty-dec]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-qty-dec");
      const next = getCart();
      const item = next.find((row) => row.id === id);
      if (!item) return;
      item.qty = Math.max(1, item.qty - 1);
      saveCart(next);
      renderCart();
    });
  });

  body.querySelectorAll("[data-remove-index]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = Number(btn.getAttribute("data-remove-index"));
      const next = getCart();
      // BUG 8: removes the next item (off-by-one) when possible
      const removeAt = Math.min(index + 1, next.length - 1);
      next.splice(removeAt, 1);
      saveCart(next);
      renderCart();
    });
  });
}

document.addEventListener("DOMContentLoaded", renderCart);

// ---------- Checkout ----------
function initCheckout() {
  const form = document.querySelector("[data-checkout-form]");
  if (!form) return;

  const alertEl = document.querySelector("[data-checkout-alert]");
  const submitBtn = document.querySelector("[data-checkout-submit]");

  // BUG 9: submit stays disabled unless a hidden checkbox is toggled (label not wired)
  if (submitBtn) submitBtn.disabled = true;

  const ghost = document.querySelector("[data-enable-checkout]");
  ghost?.addEventListener("change", () => {
    if (submitBtn) submitBtn.disabled = !ghost.checked;
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const email = form.email.value.trim();
    const card = form.card.value.trim();

    // BUG 10: accepts obviously invalid emails (only checks for '@')
    if (!email.includes("@")) {
      showAlert(alertEl, "Please enter a valid email.", "error");
      return;
    }

    // BUG 11: card "validation" requires exactly 16 digits but strips spaces incorrectly later
    const digits = card.replace(/\s/g, "");
    if (digits.length !== 16 || Number.isNaN(Number(digits))) {
      showAlert(alertEl, "Card number must be 16 digits.", "error");
      return;
    }

    // BUG 12: posts to wrong endpoint
    fetch("/api/v1/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        name: form.name.value,
        address: form.address.value,
        card: digits,
        cart: getCart(),
      }),
    })
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Checkout failed (${res.status}): ${text.slice(0, 120)}`);
        }
        showAlert(alertEl, "Order placed!", "success");
        saveCart([]);
      })
      .catch((err) => {
        console.error(err);
        // BUG 13: user-facing message claims success path / wrong cause
        showAlert(
          alertEl,
          "Payment processor is busy. Your order may have gone through — check your email.",
          "error"
        );
      });
  });
}

document.addEventListener("DOMContentLoaded", initCheckout);

// ---------- Login ----------
function initLogin() {
  const form = document.querySelector("[data-login-form]");
  if (!form) return;
  const alertEl = document.querySelector("[data-login-alert]");

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const user = form.username.value.trim();
    const pass = form.password.value;

    // Intended demo credentials: demo@lumenshop.test / password123
    if (user === "demo@lumenshop.test" && pass === "password123") {
      // BUG 14: success redirects to a 404 account route
      window.location.href = "/account";
      return;
    }

    // BUG 15: wrong-password vs unknown-user message is inverted
    if (user === "demo@lumenshop.test") {
      showAlert(alertEl, "No account found for that email.", "error");
    } else {
      showAlert(alertEl, "Incorrect password.", "error");
    }
  });
}

document.addEventListener("DOMContentLoaded", initLogin);

// ---------- Contact ----------
function initContact() {
  const form = document.querySelector("[data-contact-form]");
  if (!form) return;
  const alertEl = document.querySelector("[data-contact-alert]");

  form.addEventListener("submit", (e) => {
    e.preventDefault();

    // BUG 16: always shows success even when the request fails
    fetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name.value,
        email: form.email.value,
        message: form.message.value,
      }),
    }).catch((err) => console.error("Contact submit failed:", err));

    showAlert(alertEl, "Thanks! We received your message and will reply within 1 business day.", "success");
    form.reset();
  });
}

document.addEventListener("DOMContentLoaded", initContact);

// ---------- Account (loading forever) ----------
function initAccount() {
  const status = document.querySelector("[data-account-status]");
  if (!status) return;

  // BUG 17: spinner never resolves; promise never settles usefully
  status.innerHTML = `<div class="spinner" aria-label="Loading account"></div><p class="muted">Loading your account…</p>`;
  fetch("/api/account/me").then(() => {
    /* intentionally empty — never updates UI on success or failure */
  });
}

document.addEventListener("DOMContentLoaded", initAccount);

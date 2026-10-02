# LumenShop test web app

Intentional buggy storefront for the Insight AI agent (Playwright QA).

## Run locally

From this directory:

```bash
python -m http.server 5500
```

Then open http://localhost:5500

Or from the repo root:

```bash
python -m http.server 5500 --directory insight/test_web
```

## Pages

| Path | Purpose |
|------|---------|
| `/` | Home + broken CTA / click overlay |
| `/products.html` | Catalog, search, add-to-cart, promotions API |
| `/cart.html` | Qty controls, remove, totals |
| `/checkout.html` | Checkout form + payment POST |
| `/login.html` | Auth errors + post-login redirect |
| `/contact.html` | Contact form false success |
| `/account.html` | Infinite loading state |

## Auth note

LumenShop prints demo credentials on `/login.html`. A successful sign-in only redirects to `/account` and does not set a cookie or `localStorage`, so session capture for this origin is rejected. Leave `auth_profile_id` off the samples below. See the root README section **Auth sessions and security** for how authenticated runs work against real apps.

## Sample agent inputs

Use these as `url` + `bug_description` / `expected_behavior` for the graph:

1. **Broken shop CTA**  
   URL: `http://localhost:5500/`  
   Bug: Clicking "Shop now" does not open the product catalog.  
   Expected: Navigates to the products page.

2. **Case-sensitive search**  
   URL: `http://localhost:5500/products.html`  
   Bug: Searching for `mug` finds nothing even though Ceramic Travel Mug exists.  
   Expected: Case-insensitive match on product name.

3. **Wrong item added**  
   URL: `http://localhost:5500/products.html`  
   Bug: Adding Dot Grid Notebook puts a different product in the cart.  
   Expected: Notebook is added.

4. **Cart quantity jumps**  
   URL: `http://localhost:5500/cart.html` (add an item first)  
   Bug: Pressing + increases quantity by more than one.  
   Expected: Quantity increases by 1.

5. **Totals wrong**  
   URL: `http://localhost:5500/cart.html`  
   Bug: Discount $5 shows but total does not reflect it; tax looks too high.  
   Expected: `total = subtotal + tax - discount`.

6. **Checkout submit stuck**  
   URL: `http://localhost:5500/checkout.html`  
   Bug: Place order button never becomes clickable.  
   Expected: User can submit a filled form.

7. **Login error messages**  
   URL: `http://localhost:5500/login.html`  
   Bug: Wrong password for the demo account says the account was not found.  
   Expected: Incorrect password message.

8. **Contact false success**  
   URL: `http://localhost:5500/contact.html`  
   Bug: Form shows success even though the request fails.  
   Expected: Error state when `/api/contact` fails.

9. **Account never loads**  
   URL: `http://localhost:5500/account.html`  
   Bug: Spinner never goes away.  
   Expected: Profile loads or a clear error is shown.

See [BUGS.md](./BUGS.md) for the full planted-defect list (do not show this file to the agent under test).

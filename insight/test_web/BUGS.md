# Planted bugs (spoiler — for humans only)

Do not feed this file to the QA agent. Use it to score whether the agent found the real cause.

| # | Area | Symptom | Root cause |
|---|------|---------|------------|
| 1 | Global JS | Console error on every page | `trackPageView` is called but never defined |
| 2 | Products search | `mug` returns no results; `Mug` / `Ceramic` may differ | Filter uses case-sensitive `startsWith` |
| 3 | Products UI | Camp chair image area shows “Image 404” | Third product forced into broken image state |
| 4 | Add to cart | Notebook add confirms/adds Arc Desk Lamp | `notebook-04` remapped to `lamp-01` in click handler |
| 5 | Products network | “Promotions unavailable” + failed request | `GET /api/promotions` does not exist |
| 6 | Cart totals | Discount displayed but not applied; total too high | Total uses `subtotal + tax + tax` and ignores discount |
| 7 | Cart qty | `+` increases by 2 | Increment is `item.qty += 2` |
| 8 | Cart remove | Remove deletes the wrong row | Splice uses `index + 1` |
| 9 | Checkout | Place order stays disabled | Hidden checkbox `data-enable-checkout` must be checked; no visible control |
| 10 | Checkout email | `not-an-email@` style weak checks | Only requires `@` in the string |
| 11 | Checkout card | Spaces handled oddly vs digit count | Relies on strip + length 16 only |
| 12 | Checkout network | Order POST fails | Posts to missing `/api/v1/orders` (no `/api/orders` either) |
| 13 | Checkout UX | Failure blames “processor busy / may have gone through” | Catch block shows misleading copy |
| 14 | Login success | Valid login hits 404 | Redirects to `/account` instead of `/account.html` |
| 15 | Login errors | Wrong password ↔ unknown user messages swapped | Inverted branches in login handler |
| 16 | Contact | UI success while request fails | Success alert shown immediately; fetch error only logged |
| 17 | Account | Infinite spinner | Fetch result never updates the UI |
| 18 | Home CTA | Shop now 404s | `href="/shop"` instead of `/products.html` |
| 19 | Home clicks | CTA sometimes hard to click | Absolute `.overlay-bug` sits over action buttons |
| 20 | Login link | Forgot password 404s | `href="/reset-password"` missing page |
| 21 | Mobile nav | Clicks miss targets near the right edge | Closed drawer uses near-zero opacity but `pointer-events: auto` |

## Suggested verification order for agents

1. Reproduce via UI only (navigate → snapshot → interact).
2. Confirm with console messages and network requests where relevant.
3. Report observed vs expected — no code fixes required.

# RazorFlow AI - Agentic Commerce and Payments Platform

Repository: https://github.com/tisha-varma/RazorFlow-AI

Built solo for the Razorpay Buildathon, Track 01: AI Growth and Agentic Commerce. Public repository, master branch. LinkedIn post: about 88 reactions and about 4,900 impressions.

Idea: an AI shopping agent that understands a natural-language request, discovers products through an AI-readable merchant catalog, recommends and upsells, but never spends money without explicit user approval. The AI is good at understanding people but unreliable with money, so every money decision is made by ordinary deterministic backend code plus a human approval. "AI recommends, human approves, system verifies."

Architecture: AI -> Policy Engine -> User Approval -> Razorpay -> Payment Verification -> Order.

Key numbers: 181 automated pytest tests; 12 states in the session state machine (SessionState); about 40 REST endpoints; 10 API routers; 13 database tables.

Stack: Python, FastAPI, SQLAlchemy, Pydantic, SQLite (foreign keys on), Razorpay API in test mode with the official Python SDK, pytest; an LLM served through Groq with Gemini and a local Ollama model also supported; Next.js/TypeScript frontend with Tailwind CSS. Prices are stored as integer paise (Rs 1 = 100 paise) to avoid floating-point errors.

Database tables: Merchant, Product, ProductVariant, CommercePolicy, Cart, CartItem, Approval, Order, OrderItem, RazorpayPayment, AuditEvent, AIInteraction, SessionStateEvent.

## Purchase flow, step by step

1. POST /api/agent/chat: logs USER_INTENT_RECEIVED, sets the session state to DISCOVERING, loads chat history (rebuilt from the AIInteraction table after a server restart), and adds a live cart snapshot to the LLM context.
2. Tool-calling loop (up to 4 rounds): the LLM can request tools for searching products, getting a product, checking stock, finding related products, creating a cart, adding/removing/updating cart items and initiating checkout. The LLM only requests tools; backend code runs them. The LLM never passes a price or session id; the server supplies both.
3. Cart: CartService.add_item reads the price from the database and snapshots it on the cart line (unit_price_paise). Totals are integer sums computed in Python. Related products (for example socks with shoes) are attached as upsell suggestions and logged as UPSELL_OFFERED. The policy verdict returns with every cart change.
4. Policy engine (check_purchase_policy), checked in order: the cart exists and is not empty; it has at least one real (non-upsell) item; the total is within the minimum and maximum transaction (default maximum Rs 5,000); the per-item quantity cap (5); paid spending in the session plus the cart is within the session limit (Rs 10,000); the upsell portion is within its cap (Rs 2,000). Only paid orders count toward the session limit, so a failed payment does not use up budget.
5. initiate_checkout does not pay. It creates a pending Approval row with a random single-use token and a frozen copy of the policy, and moves the state to AWAITING_APPROVAL. A pending approval for the same amount is reused; if the cart changed, the old approval is marked expired.
6. POST /api/checkout/approve: constant-time token comparison; the token is deleted so the request cannot be replayed; the policy is re-run; the state transition is checked; the approval is marked approved and PAYMENT_APPROVED is logged. No LLM tool can perform this step.
7. /api/payment/create-order: requires an approved approval; a stale-amount check recomputes the cart total and returns HTTP 409 if it differs from the approved amount; the policy is re-checked; the server creates the Razorpay order for the server-calculated amount (the browser never sends an amount); saves the Order and RazorpayPayment rows and logs PAYMENT_ORDER_CREATED.
8. The browser opens Razorpay's checkout.js popup using only the public key id.
9. Payment is confirmed in three independent ways: (a) browser verification, where the server finds the order by the Razorpay order id stored in its own database and recomputes the HMAC signature; (b) a webhook at /api/payment/webhook for payment.captured and payment.failed, with the signature checked on the raw body, events older than 5 minutes ignored and duplicate payment ids skipped; (c) status polling at /api/payment/status/{id} for about 30 seconds.
10. _mark_order_paid sets the order to paid, empties the cart, moves the state to ORDER_CONFIRMED and writes audit events. The merchant dashboard reads the same tables.

## Other components

- Agent behaviour: fixed (non-LLM) replies for sensitive messages such as "Approval completed" and "Payment successful"; pattern-matched "show me more options" without an LLM call; provider fallback across multiple rotating Groq API keys.
- State machine: includes IDLE, DISCOVERING, RECOMMENDING, CART_BUILDING, UPSELLING, POLICY_CHECK, AWAITING_APPROVAL, PAYMENT_PENDING, PAYMENT_SUCCESS, PAYMENT_FAILED (retry returns to PAYMENT_PENDING) and ORDER_CONFIRMED; 12 states in total. Illegal transitions return HTTP 409. State lives in server memory and recover_session_state rebuilds it from the database after a restart.
- Audit log: every important event is an AuditEvent row whose hash is SHA-256(previous hash + event type + event data + actor + session id). GET /api/audit/verify reports where the chain breaks, and the dashboard shows a "chain intact" badge.
- Merchant dashboard: revenue, average order value, AI conversion (paid orders divided by AI sessions), upsell revenue, and a "with AI vs without AI" comparison. Orders are prefixed RF- (live test purchases), HIST- (seeded history) or DEMO- (scripted demos); only RF- orders count as live.
- /.well-known/ai-commerce.json: a public machine-readable description of the shop (catalog, limits, purchase flow) for other AI agents.
- Frontend pages: landing, buyer (chat, cart, approval card), merchant (dashboard, audit table) and setup (edit the policy). The session id is kept in localStorage and the approval card polls about every 2.5 seconds.
- Protection: rate limit of 120 requests per minute per IP on payment and checkout routes; secrets only in .env; the Razorpay secret key is never sent to the browser; HTTP 409/403 errors for stale, replayed or invalid actions.
- Failure handling: a failed payment marks the order failed, logs the event, uses no budget and allows a new Razorpay order on retry under the same approval; a payment confirmed after the user closes the popup is picked up by the webhook and status polling; duplicate webhook events are ignored.
- Demo scenario in the README: "marathon shoes under Rs 5,000" -> recommend RunPro Sprint (Rs 4,499) -> upsell socks (Rs 499) -> cart of Rs 4,998 passes the policy -> approve -> Razorpay test checkout -> order -> audit trail -> merchant dashboard; plus a failure scenario with no duplicate charge.

## Current limitations

No user authentication yet; session state is held in memory so only one server process can run; LLM calls are blocking; the audit chain is tamper-evident rather than unbreakable (timestamps and reason text are not hashed, there is no secret key, and concurrent writes can fork it); the "without AI" revenue figure is derived by subtracting upsell items, not from a controlled experiment.

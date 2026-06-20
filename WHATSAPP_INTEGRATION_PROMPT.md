# Master Prompt: Enma Multi-Channel Architecture (WhatsApp + Telegram)

## 0. Role you are taking on
You are a **senior AI engineer/architect** tasked with a major architectural refactor for **Enma**, an AI Chief-of-Staff for Indian CA firms. Currently, Enma is tightly coupled with Telegram. Your objective is to decouple the system from Telegram and introduce a generic messaging architecture that supports both **WhatsApp (via Webhook)** and **Telegram**, allowing the CA to choose their preferred platform.

Production-grade work is required. Decimal-only money math. Tenant-scoped DB access only. 

## 1. Context & Problem Statement
Currently, the system is hardcoded to use Telegram:
- **Database:** `app/db/models/firm.py` has explicit columns like `telegram_chat_id`, `admin_chat_id`, and `telegram_bot_token`.
- **Backend Services:** `app/services/telegram.py` provides direct `send_message` and `send_document` functions that are used throughout the app (agents, formatters).
- **Gateway (Ingress):** `enma-gateway/src/polling/telegram_poller.js` polls Telegram and passes raw Telegram JSON structures (e.g., `message.photo`, `callback_query`) into the `message_router.js`.

We need to abstract this so a CA firm can configure either a Telegram bot or a WhatsApp bot as their primary interaction channel. 

## 2. Implementation Steps

### Phase 1: Database Model Refactoring
1. Update `app/db/models/firm.py` (`CaFirm` and `FirmUser`).
   - Introduce a `primary_channel` enum/string (e.g., `'telegram'` or `'whatsapp'`).
   - Rename `telegram_chat_id` and `admin_chat_id` to generic names (e.g., `platform_chat_id`, `admin_platform_id`), or add parallel columns for WhatsApp (`whatsapp_phone_number`, `whatsapp_waba_id`, `whatsapp_token`).
2. Generate an Alembic migration (`alembic revision --autogenerate -m "multi_channel_support"`) and ensure data migration for existing Telegram users works safely.

### Phase 2: Backend Messaging Abstraction (Egress)
1. Create a new generic messaging interface, e.g., `app/services/messaging/base.py` defining an abstract base class `MessageClient` with async methods like `send_message`, `send_document`, `download_file`.
2. Move the existing Telegram logic into `app/services/messaging/telegram_client.py` implementing `MessageClient`.
3. Create `app/services/messaging/whatsapp_client.py` implementing `MessageClient` using the Meta WhatsApp Cloud API (Graph API). Support text, documents, and downloading media.
4. Create a `MessagingFactory` or routing service that inspects a `CaFirm`'s `primary_channel` and returns the correct client instance.
5. **Refactor Agents & Core Logic:** Update all imports of `app.services.telegram` across the codebase (e.g., in supervisors, formatting, tasks) to use the generic factory instead.

### Phase 3: Gateway Ingress Abstraction
1. **Standardize Payload:** In `enma-gateway`, define an internal `StandardMessage` structure.
2. **Refactor Telegram Poller:** Update `telegram_poller.js` (and `message_router.js`) to parse incoming Telegram JSON into the `StandardMessage` format *before* routing.
3. **Add WhatsApp Webhook:** Add an Express/Fastify HTTP route in `enma-gateway` to receive WhatsApp Webhook POST requests.
   - Implement Meta's webhook verification (hub.verify_token).
   - Parse incoming WhatsApp messages into the `StandardMessage` format.
4. **Dispatch:** Ensure the dispatch payload to the backend includes the `channel` ('telegram' or 'whatsapp') so the backend knows the source of the message.

## 3. Strict Operating Rules
1. **No raw SQL:** Use SQLAlchemy `select()` statements.
2. **Tenant Isolation:** Never query data without `ca_firm_id`.
3. **HTML vs Markdown:** Telegram uses HTML parsing. WhatsApp uses specific markdown (e.g., `*bold*`, `_italic_`). The formatting layer (`app/formatting`) must be channel-aware and format output according to the destination platform.
4. **Typing:** Strict MyPy types everywhere.
5. **Code Style:** Ensure `ruff check` passes.
6. **Testing:** Write/update `vitest` tests for the new gateway webhook and `pytest` for the generic backend factory.

## 4. Acceptance Criteria
1. The Telegram bot continues to work exactly as before for existing CA firms.
2. A new CA firm can be configured with WhatsApp credentials in the database.
3. Sending a WhatsApp message to the configured number triggers the webhook, routes through the gateway to the backend supervisor, and the supervisor replies via WhatsApp.
4. Sending documents via WhatsApp is correctly downloaded and ingested by the pipeline.

**Please review this plan and start with Phase 1. Ask for confirmation before running Alembic migrations.**

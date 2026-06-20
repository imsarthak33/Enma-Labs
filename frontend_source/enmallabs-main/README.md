# Enma Labs — AI Chief of Staff for CA Firms

> Automate CA onboarding, document processing, and client communication — securely, instantly, and compliantly.

[![Next.js](https://img.shields.io/badge/Next.js-15-black?style=flat-square&logo=next.js)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-blue?style=flat-square&logo=typescript)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-4.0-38bdf8?style=flat-square&logo=tailwindcss)](https://tailwindcss.com/)
[![Framer Motion](https://img.shields.io/badge/Framer_Motion-12-ff0055?style=flat-square&logo=framer)](https://www.framer.com/motion/)
[![Supabase](https://img.shields.io/badge/Supabase-Auth%20%26%20DB-3ecf8e?style=flat-square&logo=supabase)](https://supabase.com/)
[![Firebase](https://img.shields.io/badge/Firebase-12-ffca28?style=flat-square&logo=firebase)](https://firebase.google.com/)

---

## What is Enma Labs?

**Enma Labs** is an enterprise-grade AI platform built specifically for Chartered Accountant (CA) firms. At its core is **Enma** — an AI Chief of Staff that connects directly to Telegram and handles the full spectrum of CA operational work: reading and reconciling client invoices, processing bank statements, answering tax queries, and onboarding new clients through a cryptographically secure handshake — all in real time, with full legal compliance.

This repository contains the **frontend marketing and authentication website** for Enma Labs — the landing page, registration flow, login, and demo booking system.

---

## Key Features

### AI Document Processing
Clients forward invoices and bank statements directly to Enma via Telegram. Enma performs OCR extraction and tax reconciliation automatically — eliminating manual data entry entirely.

### Secure Token-Based Telegram Onboarding
Every client onboarding uses a cryptographically secure, one-time token with a 30-minute expiry window. Raw firm IDs are never exposed in any URL or deep link. The token is invalidated immediately after first use.

### Enterprise Security Architecture (V2)
The V2 architecture was rebuilt from the ground up to eliminate an IDOR vulnerability present in earlier versions. Firm authentication now flows through a secure token handshake rather than raw ID transmission.

### Legal DPA Compliance
Before any data flows, every CA firm must digitally sign a legally binding Data Processing Agreement (DPA). Consent is timestamped, IP-logged, and stored server-side — creating a permanent, auditable legal record.

### Zero LLM Training Guarantee
All uploaded client documents — invoices, bank statements, tax queries — are used strictly for the firm's authorized operations. Client data is never used to train any public AI model.

### Real-Time Tax Query Handling
Clients send GST questions, document requests, and reconciliation queries directly to Enma on Telegram. Responses are generated and delivered instantly — freeing CA teams from repetitive manual work.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 15 (App Router) |
| Language | TypeScript 5.9 |
| Styling | Tailwind CSS 4 |
| Animation | Framer Motion 12 |
| Icons | Lucide React |
| Authentication | Supabase Auth |
| Database | Supabase (PostgreSQL) |
| Real-time | Firebase |
| Forms | React Hook Form + Zod resolvers |
| Markdown | React Markdown |

---

## Project Structure

```
enmallabs/
├── app/                  # Next.js App Router pages
├── components/           # Reusable UI components
├── hooks/                # Custom React hooks
├── lib/                  # Utility functions, Supabase client, helpers
├── public/               # Static assets
├── next.config.ts        # Next.js configuration
├── tailwind.config.ts    # Tailwind CSS configuration
└── tsconfig.json         # TypeScript configuration
```

---

## Pages & Routes

| Route | Page | Description |
|---|---|---|
| `/` | Landing Page | Full marketing pitch — features, security, how it works, pricing |
| `/register` | Registration | New CA firm signup with inline DPA consent |
| `/login` | Login | Existing firm authentication |
| `/book-demo` | Book a Demo | Schedule a live 30-minute product walkthrough |

---

## Getting Started

### Prerequisites

- Node.js 20+
- npm or yarn

### Installation

```bash
# Clone the repository
git clone https://github.com/santosh5603/enmallabs.git
cd enmallabs

# Install dependencies
npm install
```

### Environment Variables

Create a `.env.local` file in the root directory and add the following:

```env
# Supabase
NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key

# Firebase
NEXT_PUBLIC_FIREBASE_API_KEY=your_firebase_api_key
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=your_project_id
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=your_project.appspot.com
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=your_sender_id
NEXT_PUBLIC_FIREBASE_APP_ID=your_app_id
```

### Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Build

```bash
npm run build
```

### Lint

```bash
npm run lint
```

---

## Security Architecture (V2)

The V2 security model follows a token-based Telegram authentication handshake:

```
1. CA firm registers → DPA consent logged server-side (timestamp + IP)
2. Backend generates: tok_{secrets.token_urlsafe(16)} — expires in 30 min
3. Token embedded in Telegram deep link — raw firm ID never exposed
4. On /start command received → token validated → Telegram ID bound to firm
5. Token immediately invalidated after single use
```

**Attack surfaces eliminated:**
- ✅ IDOR via raw firm ID in deep links — patched
- ✅ Replay attacks — one-time token + 30-min expiry
- ✅ Unsigned consent — server-side DPA logging with audit trail
- ✅ Cross-firm data leakage — logical isolation per firm

---

## Backend Integration

The frontend communicates with a FastAPI + PostgreSQL backend via the following key endpoint:

```
POST /api/v1/auth/consent
Body: { firm_id: string }
Returns: { token: string }
```

The backend also processes a secure `/start` intercept on the n8n Telegram bridge to validate tokens and bind Telegram IDs to CA firm accounts.

> Backend repository and n8n workflow configurations are maintained separately.

---

## Data & Privacy

- Client documents are used **exclusively** for OCR extraction, tax reconciliation, and authorized CA operations
- All data is logically isolated per CA firm
- All documents are encrypted at rest
- No client data is ever used to train AI models
- DPA acceptance is cryptographically logged before any data flows


---

## License

This project is proprietary software. All rights reserved © 2025 Enma Labs.

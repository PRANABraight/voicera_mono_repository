# Frontend Service

Documentation for the VoicEra Frontend — the web dashboard for managing agents, campaigns, and call analytics.

## Overview

The Frontend is the user-facing web interface for VoicEra, built with **Next.js 16.x** (App Router), **React 18+**, and **TailwindCSS 4+**. It uses the **ShadCN UI** component library and communicates with the backend exclusively through Next.js API route proxies.

**Key pages:**

- **Assistants** — Create, configure, and manage voice agents; initiate test calls
- **Campaigns** — Set up and monitor outbound call campaigns
- **Audiences** — Manage contact lists for campaigns
- **Numbers** — Provision and manage Vobiz phone numbers linked to agents
- **Knowledge Base** — Upload PDF documents for RAG-powered agent responses
- **History** — Browse past call logs; play recordings and read transcripts
- **Analytics** — View aggregated call metrics with date-range and agent filters
- **Members** — Manage organisation members
- **Integrations** — Store provider API keys (OpenAI, Deepgram, Cartesia, etc.) per organisation

---

## Getting Started

### Prerequisites

- Node.js 18+
- npm

### Installation

```bash
cd voicera_frontend

npm install
```

### Configuration

```bash
cp .env.example .env.local
# Edit .env.local with your settings
```

### Running Locally

```bash
# Development server with hot reload
npm run dev
# Open http://localhost:3000

# Production build
npm run build
npm run start
```

### Docker

```bash
docker build -t voicera-frontend .
docker run -p 3000:3000 --env-file .env.local voicera-frontend
```

---

## Project Structure

```
voicera_frontend/
├── app/
│   ├── layout.tsx                         # Root layout
│   ├── page.tsx                           # Landing / redirect
│   ├── (auth)/                            # Unauthenticated routes
│   │   ├── layout.tsx
│   │   ├── loading.tsx
│   │   ├── signup/page.tsx
│   │   ├── forgot-password/page.tsx
│   │   ├── reset-password/page.tsx
│   │   └── add-member/[slug]/page.tsx     # Member invite acceptance
│   ├── (dashboard)/                       # Authenticated routes
│   │   ├── layout.tsx
│   │   ├── loading.tsx
│   │   ├── assistants/
│   │   │   ├── page.tsx                   # Agent list + create
│   │   │   └── [id]/page.tsx              # Agent detail / edit
│   │   ├── campaigns/page.tsx
│   │   ├── audiences/page.tsx
│   │   ├── numbers/page.tsx
│   │   ├── knowledge-base/page.tsx
│   │   ├── history/page.tsx               # Call log list
│   │   ├── meetings/[meeting_id]/page.tsx # Call detail view
│   │   ├── analytics/page.tsx
│   │   ├── members/page.tsx
│   │   └── integrations/page.tsx
│   └── api/                               # Next.js API routes (backend proxy)
│       ├── login/route.ts
│       ├── agents/
│       │   ├── route.ts
│       │   └── [id]/route.ts
│       ├── analytics/route.ts
│       ├── audiences/
│       │   ├── route.ts
│       │   └── [audience_name]/route.ts
│       ├── campaigns/
│       │   ├── route.ts
│       │   └── [campaign_name]/route.ts
│       ├── get-campaigns/route.ts
│       ├── integrations/
│       │   ├── route.ts
│       │   └── [model]/route.ts
│       ├── knowledge-base/
│       │   ├── route.ts
│       │   └── [documentId]/route.ts
│       ├── meetings/
│       │   ├── route.ts
│       │   ├── [meeting_id]/route.ts
│       │   └── [meeting_id]/recording/route.ts
│       ├── outbound-call/route.ts
│       ├── phone-numbers/
│       │   ├── route.ts
│       │   ├── attach/route.ts
│       │   └── detach/route.ts
│       ├── users/route.ts
│       ├── vobiz/
│       │   ├── application/route.ts
│       │   ├── application/[application_id]/route.ts
│       │   ├── numbers/route.ts
│       │   ├── numbers/link/route.ts
│       │   └── numbers/unlink/route.ts
│       └── v1/
│           ├── members/
│           │   ├── [orgId]/route.ts
│           │   ├── add-member/route.ts
│           │   └── delete-member/route.ts
│           └── users/
│               ├── me/route.ts
│               └── [email]/route.ts
├── components/
│   ├── app-sidebar.tsx                    # Main navigation sidebar
│   ├── navigation-progress.tsx            # Page transition progress bar
│   ├── assistants/
│   │   ├── agent-card.tsx
│   │   ├── create-new-agent-card.tsx
│   │   └── test-call-sheet.tsx            # Slide-out panel for test calls
│   ├── history/
│   │   └── meeting-detail-sheet.tsx       # Slide-out for call detail / recording
│   ├── members/
│   │   └── member-card.tsx
│   └── ui/                                # ShadCN UI components
│       ├── button.tsx
│       ├── card.tsx
│       ├── dialog.tsx
│       ├── sheet.tsx
│       ├── sidebar.tsx
│       ├── table.tsx
│       ├── select.tsx
│       ├── input.tsx
│       ├── textarea.tsx
│       ├── calendar.tsx
│       ├── calendar_popover.tsx
│       └── ...
├── hooks/
│   └── use-mobile.ts
├── lib/
│   ├── api-config.ts                      # SERVER_API_URL export
│   ├── api.ts                             # API client helpers
│   └── utils.ts
├── public/                                # Static assets
├── package.json
├── tsconfig.json
├── next.config.ts
└── .env.example
```

---

## Architecture: API Proxy Pattern

The frontend does **not** call the backend directly from the browser. Instead, all data fetching goes through **Next.js API routes** (`app/api/`) which act as a server-side proxy. This pattern:

- Keeps the backend URL and JWT tokens server-side
- Allows the frontend to work behind a reverse proxy without exposing backend ports
- Provides a clean boundary for adding auth checks, request transformation, or caching

```
Browser (React)
      │  fetch /api/analytics
      ▼
Next.js API route  (app/api/analytics/route.ts)
      │  fetch http://backend:8000/api/v1/analytics
      │  (forwards Authorization header)
      ▼
VoicEra Backend
```

The backend URL is configured via `NEXT_PUBLIC_API_URL` (see Environment Variables below). The `lib/api-config.ts` module exports:

```typescript
export const SERVER_API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
```

---

## Dashboard Pages

### Assistants (`/assistants`)

List of all voice agents for the organisation. Each card shows the agent name, LLM/STT/TTS configuration, and linked phone numbers. From here you can:

- Create a new agent (opens a configuration form)
- Edit an existing agent's config
- View agent detail (`/assistants/[id]`)
- Initiate a test call via the **Test Call** slide-out panel — this triggers a Vobiz outbound call to a number you specify

The test call sheet uses `NEXT_PUBLIC_JOHNAIC_SERVER_URL` to build the Vobiz answer webhook URL when configuring the agent.

### Campaigns (`/campaigns`)

Create and monitor outbound call campaigns. A campaign links an agent to an audience (contact list) and schedules bulk outbound calls.

### Audiences (`/audiences`)

Manage contact lists used by campaigns. Each audience is a named list of phone numbers.

### Numbers (`/numbers`)

Provision Vobiz phone numbers and link them to agents. Inbound calls to a linked number are automatically routed to the associated agent.

### Knowledge Base (`/knowledge-base`)

Upload PDF documents that an agent can reference during calls (RAG). Documents are processed asynchronously — their status (`processing` → `ready` / `failed`) is shown in the list. See [Knowledge Base](knowledge-base.md) for full details.

### History (`/history`)

Browse the call log (all calls for the org). Clicking a call opens the **Meeting Detail** sheet showing:

- Duration and status
- Audio playback (WAV recording)
- Full transcript text

### Analytics (`/analytics`)

Aggregated call metrics for the organisation:

- Total calls attempted and connected
- Average call duration
- Total connected minutes
- Per-agent breakdown

Supports filtering by date range and agent type. Data is fetched from `GET /api/v1/analytics` on the backend. See [Analytics & Call Logs](analytics.md) for the full API reference.

### Members (`/members`)

Invite and manage organisation members. Members share the same org context and can manage agents, campaigns, and call data.

### Integrations (`/integrations`)

Store provider API keys at the organisation level. Keys are used by the voice server at call time (fetched via `POST /api/v1/integrations/bot/get-api-key`). See [Integrations](integrations.md) for the full list of supported providers.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | No | `http://localhost:8000` | Backend API base URL (used by Next.js API routes server-side) |
| `NEXT_PUBLIC_JOHNAIC_SERVER_URL` | Yes | — | Public URL of the Voice Server (used to build Vobiz webhook URLs when configuring agents) |

---

## Technology Stack

| Library | Version | Purpose |
|---------|---------|---------|
| Next.js | 16.x | App Router, SSR, API routes |
| React | 18+ | UI rendering |
| TailwindCSS | 4+ | Utility-first styling |
| ShadCN UI | — | Accessible component primitives |
| TypeScript | 5+ | Type safety |

---

## Next Steps

- **[Backend API](backend.md)** — REST API the frontend calls through its proxy
- **[Knowledge Base](knowledge-base.md)** — RAG document management
- **[Integrations](integrations.md)** — Provider API key management
- **[Analytics & Call Logs](analytics.md)** — Call metrics and recordings
- **[WebSocket API](../api/websocket-api.md)** — Voice streaming protocol

# VoicEra Frontend

The web dashboard for the VoicEra platform, built with **Next.js 16**, **React 18**, and **TailwindCSS 4**.

## Overview

The VoicEra dashboard provides a full management interface for:

- Creating and configuring voice AI agents
- Managing outbound campaigns and audiences
- Viewing call history, recordings, and transcripts
- Analytics and reporting
- Organisation member and integration management

## Getting Started

### Prerequisites

- Node.js 18+
- npm, yarn, pnpm, or bun
- VoicEra backend running on `http://localhost:8000`

### Installation

```bash
# Install dependencies
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with your API URL
```

### Development Server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Production Build

```bash
npm run build
npm run start
```

## Environment Variables

```env
# Backend API base URL
NEXT_PUBLIC_API_URL=http://localhost:8000

# Voice server URL (for outbound call initiation)
VOICE_SERVER_URL=http://localhost:7860

# App display name
NEXT_PUBLIC_APP_NAME=VoicEra
```

## Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| Next.js | 16 | React framework, routing, SSR |
| React | 18 | UI library |
| TailwindCSS | 4 | Utility-first styling |
| Radix UI | Latest | Accessible component primitives |
| Recharts | Latest | Analytics charts |
| Wavesurfer.js | Latest | Audio waveform playback |

## Project Structure

```
voicera_frontend/
├── app/
│   ├── (auth)/            # Public auth pages (login, signup, password reset)
│   └── (dashboard)/       # Protected dashboard pages
│       ├── agents/        # Agent management
│       ├── campaigns/     # Campaign management
│       ├── audiences/     # Audience management
│       ├── analytics/     # Analytics and reporting
│       └── meetings/      # Call history and recordings
├── components/            # Reusable UI components
├── hooks/                 # Custom React hooks
├── lib/                   # API client, utilities, constants
└── public/                # Static assets
```

## Full Documentation

See the [VoicEra documentation](../docs/index.md) for complete platform documentation including architecture, API reference, and deployment guides.

# LLM Providers

VoicEra supports multiple LLM backends for driving agent conversations. The LLM is configured per agent and the voice server instantiates the appropriate service at call time.

## Provider Overview

| Provider | Name in Config | Best For | Requires |
|----------|---------------|---------|---------|
| OpenAI | `OpenAI` | GPT-4o, general purpose, Knowledge Base | `OPENAI_API_KEY` or Integration |
| Anthropic | `anthropic` | Claude models, long context | `ANTHROPIC_API_KEY` |
| Grok (xAI) | `Grok` | xAI Grok models | `XAI_API_KEY` or `GROK_API_KEY` |
| Kenpath / Vistaar | `Kenpath` | Custom government agriculture platform | `KENPATH_JWT_PRIVATE_KEY_PATH` |

---

## Configuring LLM per Agent

The LLM is set under `llm_model` in the agent configuration:

```json
{
  "llm_model": {
    "name": "OpenAI",
    "model": "gpt-4o",
    "system_prompt": "You are a helpful voice assistant.",
    "temperature": 0.7
  }
}
```

---

## Provider Reference

### OpenAI

OpenAI's GPT models are the default and most widely used option. OpenAI is also required for the **Knowledge Base** feature (embeddings use `text-embedding-3-small`).

**Environment variable or Integration:**

```bash
OPENAI_API_KEY=sk-...
```

Or add an OpenAI Integration in the dashboard (preferred for multi-tenant deployments).

**Agent config example:**

```json
{
  "llm_model": {
    "name": "OpenAI",
    "model": "gpt-4o",
    "system_prompt": "You are a helpful voice assistant for our company.",
    "temperature": 0.7
  }
}
```

**Default model (when `model` is omitted):** `gpt-4o`

**Knowledge Base support:** Yes — when `knowledge_base_enabled: true` is set on the agent, `OpenAIKnowledgeLLMService` is used instead of the standard service. It fetches relevant document chunks from the backend RAG endpoint and injects them into the LLM context before each response.

---

### Anthropic

Anthropic's Claude models offer long context windows and strong instruction following.

**Environment variable:**

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

**Agent config example:**

```json
{
  "llm_model": {
    "name": "anthropic",
    "model": "claude-3-5-sonnet-20241022",
    "system_prompt": "You are a helpful voice assistant."
  }
}
```

**Knowledge Base support:** No — Knowledge Base retrieval is only supported with the OpenAI provider.

---

### Grok (xAI)

xAI's Grok models are accessed via the `XAI_API_KEY` or `GROK_API_KEY` environment variable.

**Environment variable:**

```bash
XAI_API_KEY=xai-...
# or
GROK_API_KEY=xai-...
```

**Agent config example:**

```json
{
  "llm_model": {
    "name": "Grok",
    "model": "grok-2-1212",
    "system_prompt": "You are a helpful voice assistant."
  }
}
```

**Default model (when `model` is omitted):** `grok-2-1212`

---

### Kenpath / Vistaar

Kenpath is a custom LLM provider built for the Vistaar government agriculture platform. It streams word-by-word text responses from a Vistaar API endpoint using JWT authentication. This provider is not a general-purpose option — it requires access to the Vistaar platform.

**Environment variables:**

```bash
KENPATH_JWT_PRIVATE_KEY_PATH=/path/to/private_key.pem
KENPATH_JWT_PHONE=+91...
KENPATH_VISTAAR_API_URL=https://voice-prod.mahapocra.gov.in  # default
```

**Agent config example:**

```json
{
  "llm_model": {
    "name": "Kenpath",
    "system_prompt": ""
  }
}
```

**Notes:**
- The `vistaar_session_id` is matched to the call's session to maintain context.
- A bilingual hold phrase is spoken if the first token takes more than 1 second.
- System prompt is typically empty for this provider as the Vistaar backend controls the conversation.

---

## Default Model Resolution

When the `model` field is omitted from the agent config, the voice server falls back to defaults defined in `voice_2_voice_server/config/llm_mappings.py`:

| Provider name | Default model |
|---------------|--------------|
| `OpenAI` | `gpt-4o` |
| `Kenpath` | (Vistaar endpoint; no model needed) |
| `anthropic` | `claude-3-5-sonnet-20241022` |
| `Grok` / `grok` | `grok-2-1212` |

---

## LLM and Knowledge Base

Knowledge Base (RAG) retrieval is tightly coupled to OpenAI:

- **Ingest:** OpenAI `text-embedding-3-small` embeds PDF chunks at upload time.
- **Retrieval:** The same embedding model is used to embed the user's question at call time.
- **Injection:** Retrieved chunks are prepended to the user message context before calling the LLM.
- **Requirement:** An OpenAI Integration (API key) must be configured for the org to enable KB features.

For details, see [Knowledge Base](knowledge-base.md).

---

## Adding a New LLM Provider

1. Create a service class in `voice_2_voice_server/services/` extending Pipecat's `LLMService`.
2. Register it in `voice_2_voice_server/api/services.py` under `create_llm_service`.
3. Add a default model entry in `voice_2_voice_server/config/llm_mappings.py`.
4. Document the required environment variables.

#!/usr/bin/env bash
# One-command Voicera install: fetches the compose stack + starts it from
# prebuilt images. No git clone, no local build.
#
#   curl -fsSL https://raw.githubusercontent.com/PRANABraight/voicera_mono_repository/main/scripts/install.sh | bash
set -euo pipefail

GITHUB_OWNER="${GITHUB_OWNER:-PRANABraight}"
GITHUB_REPO="${GITHUB_REPO:-voicera_mono_repository}"
DIR="${VOICERA_DIR:-voicera}"
ENV_FILE=".env"

fail() {
    echo "Error: $*" >&2
    exit 1
}

# --- .env helpers (lifted from scripts/start-application-services.sh so a
# re-run to upgrade never clobbers existing secrets) ---

dotenv_value() {
    local key=$1
    local line

    [[ -f "$ENV_FILE" ]] || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            "$key"=*)
                printf '%s\n' "${line#*=}"
                return 0
                ;;
        esac
    done < "$ENV_FILE"

    return 1
}

set_dotenv_value() {
    local key=$1
    local value=$2
    local tmp_file="${ENV_FILE}.tmp.$$"
    local line
    local updated=false

    if [[ -f "$ENV_FILE" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                "$key"=*)
                    printf '%s=%s\n' "$key" "$value"
                    updated=true
                    ;;
                *)
                    printf '%s\n' "$line"
                    ;;
            esac
        done < "$ENV_FILE" > "$tmp_file"

        if [[ "$updated" != "true" ]]; then
            printf '%s=%s\n' "$key" "$value" >> "$tmp_file"
        fi

        mv "$tmp_file" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" > "$ENV_FILE"
    fi
}

generate_secret() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; then
        return
    fi

    if command -v openssl >/dev/null 2>&1 && openssl rand -base64 32 | tr -d '=\n+/'; then
        return
    fi

    fail "Could not generate a secret. Install python3 or openssl, or set secrets manually in .env."
}

generate_fernet_key() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'; then
        return
    fi
    fail "Could not generate PROVIDER_AUTH_ENCRYPTION_KEY. Install python3 + cryptography, or set it manually in .env (Fernet.generate_key())."
}

# --- 1. Resolve version (before any fetch, per the design) ---

VOICERA_VERSION="${VOICERA_VERSION:-}"
if [[ -z "$VOICERA_VERSION" ]]; then
    echo "Resolving latest release tag for ${GITHUB_OWNER}/${GITHUB_REPO}..."
    tags_response="$(curl -fsSL "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/tags" || true)"
    VOICERA_VERSION="$(printf '%s' "$tags_response" \
        | grep -o '"name": *"[^"]*"' \
        | cut -d'"' -f4 \
        | grep '^v' \
        | sort -V \
        | tail -1 || true)"

    if [[ -z "$VOICERA_VERSION" ]]; then
        fail "Could not resolve a release tag from https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/tags (no tags found, or the request failed/was rate-limited). Set VOICERA_VERSION explicitly and retry, e.g.: VOICERA_VERSION=v1.0.0 curl ... | bash"
    fi
    echo "Resolved version: ${VOICERA_VERSION}"
fi
export VOICERA_VERSION

if [[ -n "${IMAGE_NAMESPACE:-}" ]]; then
    IMAGE_NAMESPACE="$(printf '%s' "$IMAGE_NAMESPACE" | tr '[:upper:]' '[:lower:]')"
    export IMAGE_NAMESPACE
fi

# --- 2. Fetch files, pinned to the resolved tag ---

RAW="https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${VOICERA_VERSION}"

mkdir -p "$DIR"
cd "$DIR"

echo "Fetching Voicera compose stack (version: ${VOICERA_VERSION})..."
curl -fsSL -o docker-compose.yaml "$RAW/docker-compose.yaml"
curl -fsSL -o .env.example "$RAW/.env.example"

# --- 3. Write secrets + version into .env (idempotent, safe to re-run) ---

if [[ ! -f "$ENV_FILE" ]]; then
    cp .env.example "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example."
fi

existing_secret="$(dotenv_value SECRET_KEY || true)"
if [[ -z "$existing_secret" ]]; then
    set_dotenv_value SECRET_KEY "$(generate_secret)"
fi

existing_api_key="$(dotenv_value INTERNAL_API_KEY || true)"
if [[ -z "$existing_api_key" ]]; then
    set_dotenv_value INTERNAL_API_KEY "$(generate_secret)"
fi

existing_enc_key="$(dotenv_value PROVIDER_AUTH_ENCRYPTION_KEY || true)"
if [[ -z "$existing_enc_key" ]]; then
    set_dotenv_value PROVIDER_AUTH_ENCRYPTION_KEY "$(generate_fernet_key)"
fi

set_dotenv_value VOICERA_VERSION "$VOICERA_VERSION"
if [[ -n "${IMAGE_NAMESPACE:-}" ]]; then
    set_dotenv_value IMAGE_NAMESPACE "$IMAGE_NAMESPACE"
fi

# --- 4. Start the stack (prod baseline only — no override file fetched) ---

echo ""
echo "This will run:"
echo "  docker compose -f docker-compose.yaml up -d"
echo ""

docker compose -f docker-compose.yaml up -d

echo ""
echo "Voicera is starting in the background."
echo "  Status:  docker compose -f docker-compose.yaml ps"
echo "  Logs:    docker compose -f docker-compose.yaml logs -f api runtime frontend"
echo "  Stop:    docker compose -f docker-compose.yaml down"

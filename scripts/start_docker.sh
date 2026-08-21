#!/usr/bin/env bash
# =============================================================================
# VoicEra — Docker Quick Start
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/COSS-India/voicera_mono_repository/main/setup-docker.sh | bash
#
# Expects docker-compose.yaml to already be present in the current directory
# (downloaded by setup-docker.sh). Generates required secrets into a single
# root .env (idempotent — re-running keeps existing values), then pulls the
# published images and starts the stack.
# =============================================================================
set -e

ENV_FILE=".env"
REGISTRY="${REGISTRY:-ghcr.io/coss-india}"
TAG="${TAG:-latest}"

fail() {
    echo "Error: $*" >&2
    exit 1
}

generate_secret() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import secrets; print(secrets.token_hex(32))'; then
        return
    fi

    if command -v openssl >/dev/null 2>&1 && openssl rand -hex 32; then
        return
    fi

    if [[ -r /dev/urandom ]] && command -v od >/dev/null 2>&1 && command -v tr >/dev/null 2>&1 && od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; then
        return
    fi

    fail "Could not generate a secret. Install python3 or openssl, or set secrets manually in $ENV_FILE."
}

generate_mongo_root_user() {
    printf 'voicera%s\n' "$(generate_secret | cut -c1-12)"
}

generate_minio_root_user() {
    printf 'voicera%s\n' "$(generate_secret | cut -c1-12)"
}

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

ensure_secret() {
    # ensure_secret KEY [default-value] -> generates+persists if missing
    local key=$1 default_value=${2:-}
    local existing
    existing="$(dotenv_value "$key" || true)"
    if [[ -z "$existing" ]]; then
        set_dotenv_value "$key" "${default_value:-$(generate_secret)}"
        echo "Created $key in $ENV_FILE."
    else
        echo "$key is already set in $ENV_FILE."
    fi
}

command -v docker >/dev/null 2>&1 || fail "Docker is required: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin is required."

[[ -f docker-compose.yaml ]] || fail "docker-compose.yaml not found. Download it first, then re-run this script."

ensure_secret SECRET_KEY
ensure_secret INTERNAL_API_KEY
ensure_secret MONGO_ROOT_USER "$(generate_mongo_root_user)"
ensure_secret MONGO_ROOT_PASSWORD
ensure_secret MINIO_ROOT_USER "$(generate_minio_root_user)"
ensure_secret MINIO_ROOT_PASSWORD
ensure_secret VOBIZ_AUTH_ID "PLACEHOLDER"
ensure_secret VOBIZ_AUTH_TOKEN "PLACEHOLDER"
ensure_secret JOHNAIC_SERVER_URL "http://localhost:3000"

echo ""
echo "Registry: $REGISTRY  Tag: $TAG"
echo ""
echo "This will run:"
echo "  REGISTRY=$REGISTRY TAG=$TAG docker compose pull && docker compose up"
echo ""

if [[ ! -t 0 ]]; then
    echo "Run the command above from an interactive shell to start VoicEra."
    exit 0
fi

read -r -p "Start VoicEra now? [Y/n]: " answer
case "$answer" in
    [Nn]*)
        echo "VoicEra was not started."
        exit 0
        ;;
esac

REGISTRY="$REGISTRY" TAG="$TAG" docker compose pull
REGISTRY="$REGISTRY" TAG="$TAG" docker compose up

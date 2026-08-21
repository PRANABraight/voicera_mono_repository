#!/bin/bash
# =============================================================================
# VoicEra — Docker Quick Start (bootstrap)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/PRANABraight/voicera_mono_repository/feature/docker-ghcr-quickstart/setup-docker.sh | bash
#
# Downloads docker-compose.yaml (published images) and scripts/start_docker.sh,
# which generates required secrets and brings up the full stack via
# `docker compose`. No git clone needed — images are pulled from ghcr.io.
#
# NOTE: points at PRANABraight's fork/branch while this is unmerged. Once
# merged to COSS-India/voicera_mono_repository@main, switch REPO/REF below
# back to "COSS-India" / "main".
# =============================================================================
set -e

REPO="${REPO:-PRANABraight/voicera_mono_repository}"
REF="${REF:-feature/docker-ghcr-quickstart}"
RAW="https://raw.githubusercontent.com/$REPO/$REF"

curl -fsSL "$RAW/docker-compose.ghcr.yaml" -o docker-compose.yaml
curl -fsSL "$RAW/scripts/start_docker.sh" -o start_docker.sh
chmod +x start_docker.sh
./start_docker.sh

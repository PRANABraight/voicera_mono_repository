#!/bin/bash
# =============================================================================
# VoicEra — Docker Quick Start (bootstrap)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/COSS-India/voicera_mono_repository/main/setup-docker.sh | bash
#
# Downloads docker-compose.yaml (published images) and scripts/start_docker.sh,
# which generates required secrets and brings up the full stack via
# `docker compose`. No git clone needed — images are pulled from ghcr.io.
# =============================================================================
set -e

curl -fsSL https://raw.githubusercontent.com/COSS-India/voicera_mono_repository/main/docker-compose.ghcr.yaml -o docker-compose.yaml
curl -fsSL https://raw.githubusercontent.com/COSS-India/voicera_mono_repository/main/scripts/start_docker.sh -o start_docker.sh
chmod +x start_docker.sh
./start_docker.sh

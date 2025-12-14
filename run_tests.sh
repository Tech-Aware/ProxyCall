#!/usr/bin/env bash
set -euo pipefail

# Script unique pour exécuter l'ensemble de la suite de tests
# Utilisation : ./run_tests.sh

project_root="$(cd "$(dirname "$0")" && pwd)"
cd "$project_root"

python -m pytest tests "$@"

#!/bin/bash
set -e

# mise
eval "$(mise activate bash)"
mise fmt
mise install

# Shared lint tasks
mise run gha-lint

# Check for uncommitted changes
git diff --exit-code

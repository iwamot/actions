#!/bin/bash
set -e

# mise
eval "$(mise activate bash)"
mise fmt
mise install

# Release tag checks
python3 -B -m unittest discover -s release-tag -p 'test_*.py'

# Shared lint tasks
mise run gha-lint
mise run shell-lint

# Check for uncommitted changes
git diff --exit-code

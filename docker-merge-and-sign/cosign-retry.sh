#!/bin/bash
# Run cosign, once more if the transparency log rejects the upload as a
# duplicate of an entry it already holds.
#
# cosign retries its own Rekor upload, and a retry repeating an upload the log
# already accepted comes back 409 (sigstore/cosign#3356). Treating that as done
# would be wrong: the entry is in the log, but cosign exits before attaching
# anything to the registry, so the image would be left without the signature or
# attestation this step exists to add. Running the command again signs with a
# fresh ephemeral key, which is a different entry and no longer a duplicate.
set -euo pipefail

log=$(mktemp)
trap 'rm -f "$log"' EXIT

for attempt in 1 2; do
  if cosign "$@" 2>&1 | tee "$log"; then
    exit 0
  fi
  if ! grep -q "already exists in the transparency log" "$log"; then
    exit 1
  fi
  echo "Attempt ${attempt}: the transparency log rejected the upload as a duplicate."
done

exit 1

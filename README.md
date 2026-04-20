# actions

iwamot's shared GitHub Actions composite actions.

## Actions

| Action | Purpose |
|--------|---------|
| `mise-validate` | Checkout caller repo, setup mise from `mise.toml`, and run `validate.sh`. |

## Usage

Each action is invoked from a workflow step via `uses:`.

### `mise-validate`

Expects a `mise.toml` with `min_version` at the caller's repository root and a `validate.sh` script. This action handles checkout internally, so the caller should not run `actions/checkout` before it.

```yaml
jobs:
  validate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: iwamot/actions/mise-validate@<sha> # vX.X.X
```

## Validation

```bash
./validate.sh
```

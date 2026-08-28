# Kaggle

The GPU execution backend.

## Credentials

**Settings, API tokens, Legacy API Credentials, Create Legacy API Key**, then place
`kaggle.json` at `~/.kaggle/kaggle.json`. Older guides describing "Account, Create New
API Token" describe a flow that no longer exists.

Do not tighten the file's ACL with `icacls /inheritance:r`; that locks the Kaggle CLI
out of its own credentials.

## Packaging

Research source stays as `.py` and `.yaml` in git. Notebooks are **generated packaging
artifacts**, not the editable source. Both `notebook` and `script` kernel types are
supported.

`kernel-metadata.json` field names were taken from `kaggle kernels init` output rather
than assumed. Booleans must be lowercase strings, which is why the spec object
serialises them explicitly.

## Status mapping

| Kaggle worker state | Internal |
|---|---|
| `QUEUED` | `submitted` |
| `RUNNING` | `running` |
| `COMPLETE` | `complete` |
| `ERROR` | `error` |
| `CANCEL_*` | `cancelled` |

An unrecognised state maps to `unknown` and is left alone rather than guessed into a
wrong status.

## Quota

Kaggle does not expose remaining GPU hours through the CLI, so no figure is displayed.
Exhaustion is detected when a push or status call reports it, and raises
`QuotaUnavailable` distinctly from `KaggleError` so the caller routes to the external
bundle instead of retrying a run that cannot succeed.

## Not yet verified

`kernels push`, output retrieval and log download have not been exercised against real
Kaggle. See [STATUS.md](STATUS.md).

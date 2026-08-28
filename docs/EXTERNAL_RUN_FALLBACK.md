# External run fallback

When Kaggle GPU quota is exhausted, retrying cannot succeed. Instead the experiment is
packaged for someone else to run on **their own account**.

This is intentionally human-in-the-loop. The system never automates a second Kaggle
account and never handles anyone else's credentials.

## Export

```bash
researchos bundle EXP-0038 --code notebooks/generated/exp-0038.ipynb --accelerator "GPU T4 x2" --runtime "~6 GPU-hours"
```

Produces `EXP-0038-run-bundle.zip` containing the code artifact, a manifest, and a
README stating accelerator, internet setting, dataset and estimated runtime, plus an
instruction not to change hyperparameters.

## Import

```bash
researchos import returned.zip --experiment EXP-0038
```

Validation happens before anything touches the database, because attaching a foreign
or altered result to the wrong experiment corrupts the record silently, which is worse
than a failed import:

- The manifest must name the experiment it claims to be
- The config hash must match, if one was recorded
- At least one numeric metric must be present
- Booleans and strings are dropped with a warning
- `metrics.json` is found anywhere in the archive, since Kaggle nests output dirs

The result attaches as a run with backend `external_manual`, so provenance records
that it was not executed on your account.

# KOSPI Shadow Coach v4.2 verification

## Added

- Final probability decomposition into training prior, raw model probability, and model weight.
- Up to three local positive and negative drivers.
- Explicit prior-only explanation when validation shrinks the raw model weight near zero.
- Korean feature labels and current-value display.
- Non-causality and correlated-feature limitation notice.

## Validation

- `PYTHONPATH=src pytest -q`: **29 passed**
- GitHub Actions YAML: **6/6 PyYAML parsed**
- `node --check app/app.js`: **PASS**
- `node --check app/sw.js`: **PASS**
- `python -m compileall -q src tests`: **PASS**

`actionlint` was not installed in the local packaging environment, so it was not claimed as run here.

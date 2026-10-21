# Data contracts

Paths in configuration files are relative to the configuration file directory.
IDs must be strings, nonempty and unique in each table. Preserve stereochemistry,
salt form and target identity across inputs.

## Library

CSV or Parquet. Required: `compound_id`, `smiles`; optional `name`, `inchikey`.
Use `library.columns` to map existing column names. Supply canonical isomeric
SMILES with one row per structure, before model inference. No salt stripping,
stereochemistry removal or tautomer merging is implied.

Normalized database CSVs can be assembled with:

```bash
python3 run.py prepare-library --inputs /data/coconut.csv /data/lotus.csv --output libraries/prepared
```

Install the `chemistry` extra for this command. The input accepts `smiles` or
`canonical_isomeric_smiles`, and optional `name`, `source_database`, `compound_id`.
Outputs are `molecules.csv`, `source_records.csv` and `rejected.csv`.
Generated IDs follow sorted canonical SMILES and are specific to that library
release; adding structures can change IDs. Regenerate or remap model predictions
and embeddings when the library changes. This importer reads CSV exports; it
does not download or parse every database's native archive format.

## Model scores

One CSV per model: `compound_id,raw_score,status,error`. The required first three
fields must cover the exact same full library ID set, including failed rows.
All four score directions are higher-is-better. Failed or nonfinite rows remain
in the input file and are excluded from the valid percentile reference pool.
The percentile reference input is the complete valid score pool for the library.

## ADMET

`compound_id`, `admet_status`, and these endpoints:

| Field | Weight | Utility |
|---|---:|---|
| `BBB_Martins` | 25 | p |
| `PAMPA_NCATS` | 10 | p |
| `Solubility_AqSolDB` | 10 | clip((logS+6)/4,0,1) |
| `hERG` | 20 | 1-p |
| `DILI` | 20 | 1-p |
| `CYP3A4_Veith` | 5 | 1-p |
| `CYP1A2_Veith` | 5 | 1-p |
| `Pgp_Broccatelli` | 5 | 1-p |

LogS uses log10(mol/L). The P-gp endpoint is inhibition; substrate status has a separate definition. Missing or
failed predictions leave the final score empty. ADMET8 is a computational prioritization score derived from the eight
predicted endpoints above; nasal exposure and safety use separate measurements.

## Gate

`compound_id,gate_status,gate_class,gate_fine_class`, plus optional raw evidence
and rationale columns. `gate_fine_class` takes precedence when present; preserve
E/E1/E2. Unknown labels, missing rows or execution failures are classified as E2
for the application, with the supplied raw fields retained.

Route-follow-up accepts complete-score A1/A2/B1/B2/B3/C1/C2/D1/D2 candidates.
The distinct deliverable-now policy accepts A1/A2/B1/B2 only. E classes have an
empty/unknown deliverable-now field. Formal
benchmark metrics that map E to negative are outside this postprocessing step.

## Saved-prediction mode

Copy `configs/demo.json` and replace its library, output, `run.top_k` and `inputs`
paths with real files. Set `inputs.kind` to `imported_predictions` and
`run.top_k` to 1000. Run `postprocess` into a fresh output directory.
The command recomputes ranking and stratification but invokes none of the
predictors that produced the input tables. The real pipeline's `gate` stage only
assesses complete-score candidates; imported extra Gate rows do not enter the
ranked output when composite inputs are incomplete.

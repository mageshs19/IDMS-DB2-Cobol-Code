# IDMS DB2 Phase 2 Converter

Standalone Phase 2 project for converting IDMS COBOL programs into DB2 embedded SQL COBOL.

## Inputs

- Sheet Mapping Excel or CSV
- DCLGEN text file or multiple DCLGEN files
- Copybook text file
- Optional Copybook PDF
- IDMS COBOL source code

## Output

- Converted DB2 COBOL code
- Validation messages
- Metadata overview
- Record summary
- Column summary
- Set / relationship summary
- Sheet mapping preview

## Run

```bash
set PYTHONPATH=src
python -m streamlit run src/idms_db2_phase2/app.py --server.port 8502

python src\idms_db2_phase2\testing\run_retrieval.py
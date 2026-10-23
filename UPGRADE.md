# Replace the previous black-box version

1. Stop Streamlit with Ctrl+C in its terminal.
2. Download the latest `health-deid-masking` branch ZIP and extract it into a temporary folder.
3. Copy the new source files over the matching paths in your existing project. Keep your existing `.env` and entire `.deid-data` folder, especially `vault.key`; do not delete or replace them. Do not overwrite your own real patient records.

The changed application files are:

```text
app.py
agents/deid_agent.py
engine/documents.py
engine/service.py
engine/vault.py
utils/ocr_handler.py
utils/pdf_handler.py
utils/field_layout.py   ← new file; copy this too
requirements.txt
requirements.lock
```

`OCRHandler.redact()` in `utils/ocr_handler.py` performs the background-matched erasure. `utils/field_layout.py` finds the associated labels. `engine/documents.py` aligns the whole field and creates clean public text. The vault/service changes keep restoration text private. Copy all the listed files together; replacing only the black rectangle drawing line will leave field labels and text placeholders behind.

4. Activate your existing Python environment and update packages:

```sh
python -m pip install -r requirements.lock
```

5. For scans, install Tesseract OCR using the [official installation instructions](https://tesseract-ocr.github.io/tessdoc/Installation.html). A standard Windows installation is found automatically. If it is installed elsewhere, add the executable path to your existing `.env`:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

6. Restart:

```sh
python -m streamlit run app.py
```

7. Upload and scan the original report again, review Preview clean report, and share the new copy. An old attachment with black rectangles stays unchanged until you generate a new report. Existing admin and doctor accounts and old encrypted records continue to work.

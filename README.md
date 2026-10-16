# MediScript AI — pricing-corrected build

## What changed

- Medicine prices now come from the supplied `medicine.csv`, including the listed unit or package basis.
- Prices are left unavailable when the exact brand/form/strength cannot be identified. The old hard-coded generic estimates and artificial alternative discounts were removed.
- Test rates and general test purposes now come from the supplied `testrate.csv` columns (`Test name`, `Popular Diagnostic rate (BDT, photo)`, `Government rate (BDT)`, and `Why the test is done (general information)`). Gemini is instructed to transcribe tests and never invent costs.
- The updated `medicine.csv`, `testrate.csv`, and `doctors.csv` are loaded directly by the app for medicine matching/pricing, test rates, and the doctor directory.
- The included virtual environment and `.env` credentials from the source archive are not included.
- Added a centered responsive CSS layout, left-aligned clickable stethoscope branding, pill navigation, fixed centered footer, subtle medical animation, and a project-focused About page.
- Doctor directory is the opening page; Prescription decoder is second. Doctor search recognizes common Bangla, English, and Banglish symptom names, tolerates close spelling mistakes, suggests the matched specialty in the filter, and still supports doctor-name search. Doctors are paginated (10 per page) with numbered page buttons plus previous/next controls.
- Medicine indexes are cached, and a prescription image is analyzed once per browser session; switching tabs or changing a filter will reuse that result.
- A consent dialog appears before showing file upload/camera controls. Users can choose a file, drag and drop it, or take a camera image. The app does not persist the image or patient fields; the in-memory audit contains timestamp, shortened image hash, and item counts only.
- The consent dialog is centered; file click and drag/drop share one upload zone, while camera capture is in a compact expander. The brand links back to the Doctor directory opening page and heading permalink icons are hidden.
- An optional second Gemini read is under Advanced verification, makes another API request, and shows field agreement; agreement is not proof of correctness.
- Medicine results show heuristic match scores based on supplied-dataset matching. The diagnosis text score is the model estimate of handwriting readability only, not clinical diagnostic accuracy. Score labels use a high-contrast badge for readability.
- The report downloads directly as a PDF with bilingual text, medicine-use explanations, test purposes, rates, and cited web sources. The project bundles the OFL-licensed Noto Sans Bengali font and uses HarfBuzz shaping with word-aware wrapping so Bengali renders without breaking words on Windows and Linux deployments.
- Medicine alternatives are restricted to matching generic/form/strength dataset rows. They are not recommendations that the medicines are clinically interchangeable; ask a clinician/pharmacist.
- Missing medicine prices or test rates are marked as unavailable in the supplied dataset. No rate is guessed.
- General medicine uses are requested from Gemini with Google Search grounding using medicine and catalog generic names only, then displayed with cited source links. Test purposes are read from `testrate.csv`; test names are not web searched. The prescription image and patient fields are not included in those web searches. Search use may incur API charges.
- Each medicine card shows up to 3 alternatives at a time. Its refresh button cycles through additional dataset matches, when available. The footer credits Habibur Rahman Shakib (HRS).
- Medicine match progress now sits in the medicine column and can reach 100% for an exact catalog-name match. Alternative refresh is isolated in a Streamlit fragment, so it updates only that medicine's alternatives. Medicine lookup, displayed price, test rate, and Google-grounded explanation results are cached to reduce repeated work.
- Grounded use/reason text is retained when the model returns JSON with surrounding text or omits source URLs. Explanations appear in the medicine/test card, with an in-card note if no reliable explanation is returned. Progress meters display their labels over the bar, and heading link icons are hidden. Background colors, card surfaces, progress bars, and hover states were refreshed with reduced-motion support.
- Medicine-use explanations use Gemini's schema-constrained JSON response together with Google Search grounding and catalog generic names. Returned items map back to their medicine rows even if the model omits its index. The schema version refreshes cached explanations after changes. No fixed 95% accuracy is claimed; the displayed match scores measure dataset text matching, not clinical or explanation accuracy.
- Prescription images are EXIF-rotated correctly and sent at up to 2400px on the longest edge. OCR runs in a background worker so the page remains responsive. It uses Gemini 3.8 Flash first and Gemini 3.5 Flash as fallback, with the Google Gen AI SDK's default retry policy and a 25-second per-request timeout. Google-grounded medicine descriptions run separately in the background after OCR.
- The distributable runtime package keeps only app code, required CSV catalogs, setup files, and documentation. Legacy SQLite builders, test scripts, unused workbooks, and the unused database file are excluded from the ZIP.

## Run on Windows

1. Copy `.env.example` to `.env` and add your Google AI Studio key as `GEMINI_API_KEY=...`.
2. Double-click `run.bat`. It creates a virtual environment and installs the listed dependencies on the first run.
3. Open the local Streamlit URL shown in the terminal.

Medicine prices and test rates are the supplied dataset values and may be outdated. Confirm current prices with a pharmacy or diagnostic center.

## Final-year project evaluation ideas

For the report/viva, build a labeled test set with consent and remove personal identifiers. Compare OCR output with manually transcribed ground truth using character error rate (CER), word error rate (WER), medicine-name exact match, and strength/dose field accuracy. Report sample size and failure cases. Do not call the UI heuristic confidence score model accuracy; calibrating confidence requires a separate held-out labeled set and calibration analysis.

Suggested evaluation tables: (1) primary model vs. second-read agreement, (2) extracted-field exact match vs. ground truth, (3) error categories such as name/strength/dose/date, and (4) score bands (0–59, 60–79, 80–100) vs. actual accuracy. Use the fourth only after collecting enough labeled examples; otherwise mark it as future work.

This prototype is an information and dataset-matching aid, not a diagnostic or prescribing system. A qualified clinician must verify all extracted values against the original prescription.

Prescription reading still depends on Gemini API response time and availability. A separate FastAPI layer would not make Gemini's external inference faster; this build avoids repeating the same API request during Streamlit reruns.

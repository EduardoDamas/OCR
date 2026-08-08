# Prompt — Rebuild the Bolão OCR/OMR Desktop App (clean rebuild)

Paste this into a coding agent (Claude Code / Cursor). Build incrementally, phase by
phase, and VALIDATE each phase against real labeled pages before moving on. Everything
below is distilled from a working prior version — the "PITFALLS" and "DEAD ENDS"
sections are hard-won; respect them to avoid burning weeks.

---

## 0. What we are building

A **fully offline desktop app** (Windows `.exe`, no Python needed on the target) that
reads **photographed/scanned Brazilian football-pool cards ("bolão de coluna, 8 jogos")**,
extracts each card's 8 game picks, scores them against official results, and produces a
ranking + Excel/CSV export. It must work on **tilted phone photos**, not just clean scans.

**Stack:** Python 3.12, OpenCV (`opencv-python`), NumPy, PyMuPDF (`fitz`) for PDF render,
openpyxl for Excel, tkinter for the desktop UI, Flask (optional) for a web ranking page,
PyInstaller for the single-file `.exe`. Optional: PyTorch **only** for training a model
offline (never at runtime — inference runs through `cv2.dnn` on an ONNX file).

---

## 1. The card format (measure everything against this)

- Source: a **multi-page PDF or image files** (phone photos, frequently skewed/warped).
- Page layout: **4 columns × 6 rows = 24 cards per page**. A **title banner** sits at the
  top of the page (e.g. "BOLÃO DA RESSACA — DIA xx/xx", "TODA SEGUNDA").
- Each card: **8 game rows**; each row has **3 options: Casa / Empate / Fora**.
  - The printed layout is two team-name columns with mark boxes at fixed fractions of the
    **card width**: **Casa ≈ far-left**, **Empate ≈ centre divider**, **Fora ≈ far-right**.
- Marks are **any style**: handwritten X, stroke, tick, circle; blue OR black pen/pencil;
  and on some sheets **printed filled-square checkboxes (■)**.
- A card may be **fully filled** (all 8 games marked). A blank game = unreadable/error.

**There are TWO real-world sub-formats — handle both:**
- **Format A "coluna/handwritten"** (the main production format: SEGUNDA / TERÇA / QUINTA
  "Bolão de Coluna 8 Jogos"): handwritten marks, located by table-line detection.
- **Format B "EXTRA/system"**: printed checkbox sheets / faint photocopies where the table
  lines vanish; located by **template registration** (see §4).

---

## 2. Preprocessing pipeline (order matters)

For every page: `render → normalize_width → perspective-correct → global deskew → CLAHE`.

- **`normalize_width` is CRITICAL and non-negotiable.** Resize every page to a fixed
  **~1654 px width** before any detection. All thresholds (Canny, adaptive-C, Hough
  lengths) are tuned for that width. Feeding native high-res (3000–4000 px phone photos)
  **collapses accuracy** (measured: 73% at 1654px → 47% at 3308px → ~33% on raw photos).
  Normalizing to 1654 recovers ~71% at ANY input resolution. Render PDFs at ~300 dpi then
  normalize (or render nearer 1654px directly to save time).
- Keep a **BGR copy aligned to the grayscale** one — colour is needed for blue-ink masks.
- Global deskew (Hough on near-horizontal lines) removes the sheet's overall rotation.

---

## 3. Card detection & cell location (Format A)

1. **Content bbox:** find the printed-grid bounding box from horizontal+vertical line masks
   (morphology). **THEN strip a leading TITLE band:** the title is a short cluster of *wide*
   horizontal rules near the top, separated from the first card row by a vertical gap.
   If you don't strip it, even-division counts the title as card-grid height and the whole
   **top row of cards lands on the header → reads blank**. Make the strip self-guarding
   (only a short band near the top; never strip >½ the box) so it's a no-op on flat pages.
2. Divide the (title-stripped) bbox into the **4×6 grid**. Crop each card with **generous
   vertical + horizontal headroom** so perspective drift never clips a row or a mark column.
3. **Per-card LOCAL deskew:** each crop still has a residual perspective slant that *varies
   across the page* (worst at top rows/corners). Rotate each crop flat (Hough on its own
   near-horizontal edges) BEFORE locating rows — this alone was +20 accuracy points.
4. **Locate the 8 game rows** inside each crop from the **run of 9 most evenly-spaced
   horizontal table lines** (not fixed fractions). Fall back to fixed fractions only if
   lines can't be found.
5. **Locate the Casa/Empate/Fora columns** from the vertical lines: the two widest gaps are
   the team-name columns; the 3 mark boxes are the narrow gaps left-of / between / right-of
   them. Choose the team pair straddling the crop centre (robust to horizontal drift).

---

## 4. Format B (EXTRA / faint sheets) — template registration

When the printed table lines vanish (faint photocopies, printed-checkbox sheets), line
detection fails. Solve **localization as REGISTRATION, not detection**, because every EXTRA
sheet is the identical printed form:

- Build a **template ONCE** from a clean exemplar: define the exact 24×8×3 = 576 sample
  points via line detection, store them in the exemplar's coordinates + the reference image.
- For each page, align it to the exemplar with **coarse-to-fine pyramid ECC**
  (`cv2.findTransformECC`, intensity/gradient based — works on faint scans where ORB fails):
  seed with a form-bbox affine, refine affine → homography at the finest levels; warp the
  template's 576 points into the page; sample ink.
- Bundle **2–3 diverse exemplars** (clean-square, clean-handwritten, faint-handwritten). A
  page registers to whichever gives the best ECC correlation `cc`. `cc` is a clean
  confidence signal (cc>0.6 ⇒ great; cc<0.45 ⇒ didn't lock).
- This lifts faint-sheet accuracy from ~50% (line detection) to **~95%+**.

---

## 5. Format auto-detection (do it fast — this bit the prior version hard)

A whole file is ONE format. Detect **once on page 1**, apply to all pages.

- **Cheap probe first:** affine-only ECC (no homography), coarse levels, few iterations,
  early-out. ~3 s. If the probe cc is clearly low ⇒ Format A (`segunda`) immediately.
- **Confirm only if the probe passes**, with the full registration, and require a **CLEAR
  lock** to call it Format B.
- **Threshold separation (measured, keep margin):** the coluna/handwritten family gives
  page-1 probe ≤ ~0.51 and confirm cc ≤ ~0.46; EXTRA gives probe ≥ ~0.55 and confirm cc
  ≥ ~0.67. Set `PROBE_REJECT ≈ 0.52` and `EXTRA_DETECT_CC ≈ 0.55`.
- **PITFALL that caused "processing takes hours":** if the threshold is too low, a coluna
  page sits just over it, runs the full 60 s confirm, *and* misroutes to the slow EXTRA
  reader for all N pages (hours + wrong format). Bias detection toward Format A — it's the
  production format; EXTRA is secondary.

---

## 6. Mark reading & confidence

- Build two ink masks per card: **blue mask** (HSV blue + blue-channel excess) and **dark
  mask** (adaptive threshold, mid-grey range to suppress printed text). Cell score =
  `max(blue×2, dark)` fill-density (blue weighted for specificity).
- Per game, pick the option with the highest score; confidence = separation between best and
  runner-up. `choice=None` if nothing is marked.
- **`needs_review` flag** when confidence < threshold. Make the threshold a **single tunable
  constant** (`CONFIDENCE_LOW`). Measured tradeoff on tilted photos: 0.50 → ~31% of cards
  flagged; **0.40 → ~21% flagged with almost no extra silent errors** (false alarms drop
  first). Below ~0.40 the only remaining flags are genuinely-blank reads (keep those).
- **Optional ML:** a per-game CNN (3-channel: blue mask + colour-saturation mask + dark
  mask, ~20×80, shift augmentation) trained offline, exported to **ONNX**, run at runtime
  via `cv2.dnn` (legacy exporter for cv2 compat; no torch at runtime). It added ~+10 pts on
  handwritten pages. Auto-use the model if the `.onnx` is bundled, else fall back to the CV
  scorer. **Lesson: once localization is good, ML adds little — fix localization first.**

---

## 7. Output, scoring, ranking

- **Export Excel + CSV:** one row per card — `Participante | Página | Card | Jogo1..Jogo8
  (Casa/Empate/Fora/?) | Revisão(SIM) | Total`. Highlight review rows.
- **Scoring:** user enters the **8 official results** (Casa/Empate/Fora, plus **Anular** for
  a voided game that counts for nobody and drops the max). Score = correct games; a
  duplo/triplo counts as correct if the official result is among the marked options.
- **Ranking** with tie handling + a **live distribution** ("how many cards have 8, 7, 6…").
- **Web ranking (optional):** a tiny Flask page at a shareable URL, mobile-friendly, updates
  after results are entered. No app install.

---

## 8. Desktop UI (tkinter)

- Session bar (create/select session), **Select PDF/Image → Process** (progress bar + log),
  **Export Excel / Export CSV**, an **Official Results** panel (8 games × Casa/Empate/Fora/
  Anular + "Update Ranking"), and a **ranking table**.
- Processing runs on a background thread; never block the UI.

---

## 9. Persistence & packaging

- **SQLite** DB. When frozen, put the DB **next to the executable** (`sys.executable`
  parent), because the PyInstaller temp dir is wiped on exit. Sessions do NOT auto-reload
  (start with no session) to avoid exporting stale cached results.
- **PyInstaller onefile**, `--windowed`. Bundle `datas`: web templates + the models/template
  assets (`.onnx`, template `.npz`). `hiddenimports`: `fitz`, `openpyxl`, tkinter submodules.
- **Ship-verification lesson (this wasted days):** after building, VERIFY the actual binary
  contains your change — extract the PYZ and check the bytecode
  (`PyInstaller.archive.readers.CArchiveReader`+`ZlibArchiveReader`, look for your function
  names). Multiple stale `.exe` copies caused "the fix does nothing" repeatedly. Always
  hand the user ONE freshly-named, verified `.exe`, and give them a self-check (e.g. "the new
  build processes N pages in ~T seconds; if it's slower it's the old one").

---

## 10. Accuracy targets & honest limits

- Flat/straight scans: **~90–95%** per game. Tilted phone photos: classical CV ceils around
  **~77%**, template-registration/ML lift specific formats to **~95%**. Errors concentrate
  where perspective is worst (top rows, corners). **Better input (flatter, less-angled
  scans) is the single biggest lever** — say so to the user; don't over-promise on bad photos.
- The review flag catches ~50% of errors on tilted photos; over-flagging is a deliberate
  safety choice in a money context — expose it as a tunable, don't hardcode it high.

---

## 11. DEAD ENDS — do NOT re-attempt (all measured, none helped)

- Blindly increasing render DPI (see §2 — it HURTS without normalization).
- Scoring-formula tweaks (blue-only, etc.), table-window "topmost vs min-spread" strategies,
  confidence-threshold-only silent-error filtering, relaxing the column constraint — all no
  gain over the local-deskew + line-run approach.
- ORB/feature homography on faint text (few inliers, erratic ~43%). Use ECC.
- Per-card residual ECC refinement after global registration (it snaps onto the handwritten
  ink and pulls the grid OFF the printed cell — a net wash that regresses clean pages).
- Warp-standardization and heavy augmentation for the ML model (hurt; plateau is data/label
  bound, not model).

---

## 12. Build order (validate each before the next)

1. Preprocess + `normalize_width` + card detection (§2–3). **Gate:** on a labeled page,
   cells visibly land on the right boxes for all 24 cards, incl. the top row (title strip).
2. Mark reading + CSV export (§6–7). **Gate:** per-game accuracy vs a labeled page ≥ ~85%
   on a reasonably flat scan.
3. Format B registration + auto-detect (§4–5). **Gate:** an EXTRA file routes to EXTRA and
   reads ≥ ~95%; a coluna file routes to `segunda` in a few seconds (not minutes).
4. Scoring + ranking + web page (§7). 5. Desktop UI (§8). 6. Packaging + ship-verification (§9).
7. Optional ML (§6).

**Testing discipline:** keep a few pages of **client-confirmed ground truth**; measure
per-game accuracy and leave-one-page-out; add unit tests so refactors can't silently
regress. Log any coverage you drop (top-N, no-retry) — silent truncation reads as "done".

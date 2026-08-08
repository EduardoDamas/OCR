"""
Measure the vision-model card reader against the two client-confirmed pages.

Setup (one time):
    pip install anthropic
    set ANTHROPIC_API_KEY=sk-ant-...        (Windows)   or  export on bash

Run:
    python scripts_vlm_measure.py
    python scripts_vlm_measure.py claude-sonnet-4-6      # try a cheaper model

It reads the card_*.png crops + ground_truth.py already in
samples/ground_truth/page1 and page2, calls the model per card, scores against
the confirmed answers, and prints accuracy + token usage so you can see the
real accuracy AND the real cost per page before committing.
"""
import sys, os, glob, importlib.util
sys.path.insert(0, os.path.dirname(__file__))
import cv2

from src.omr.vlm_reader import read_card_vlm, DEFAULT_MODEL

MODEL = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
LABEL = {0: "Casa", 1: "Empate", 2: "Fora", "Duplo": "Duplo", None: "?"}


def load_gt(page):
    p = f"samples/ground_truth/page{page}/ground_truth.py"
    spec = importlib.util.spec_from_file_location("gt", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.GROUND_TRUTH


def main():
    try:
        import anthropic
    except ImportError:
        print("Missing SDK. Run:  pip install anthropic"); return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set your key first:  set ANTHROPIC_API_KEY=sk-ant-..."); return
    client = anthropic.Anthropic()

    print(f"Model: {MODEL}\n")
    grand_ok = grand_tot = in_tok = out_tok = 0
    for page in [1, 2]:
        gt = load_gt(page)
        folder = f"samples/ground_truth/page{page}"
        ok = tot = 0
        for n in range(1, 25):
            f = f"{folder}/card_{n:02d}.png"
            if not os.path.exists(f):
                continue
            img = cv2.imread(f)
            try:
                marks, usage = read_card_vlm(img, client, MODEL)
            except Exception as e:
                print(f"  page{page} #{n}: ERROR {e}"); continue
            in_tok += usage["input_tokens"]; out_tok += usage["output_tokens"]
            for g in range(8):
                tot += 1
                # Duplo counts as correct only if GT also voided (not in current GT)
                if marks[g] == gt[n][g]:
                    ok += 1
        grand_ok += ok; grand_tot += tot
        print(f"Page {page}: {ok}/{tot} ({ok/max(tot,1)*100:.0f}%)")

    print(f"\nTOTAL: {grand_ok}/{grand_tot} ({grand_ok/max(grand_tot,1)*100:.1f}%)")
    print(f"Tokens: {in_tok} in + {out_tok} out  over 48 cards")
    print(f"  ≈ {in_tok/48:.0f} in / {out_tok/48:.0f} out per card "
          f"({in_tok/48*24:.0f} in per 24-card page)")
    print("Compare: classical CV baseline = 297/384 (77%)")


if __name__ == "__main__":
    main()

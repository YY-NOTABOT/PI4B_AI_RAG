from __future__ import annotations

import ast
import csv
import re
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "whisper_large_full.log"
OUT_DIR = ROOT / "whisper_inference_results"


def parse_metrics(log_text: str) -> tuple[list[dict], list[dict]]:
    train_rows: list[dict] = []
    eval_rows: list[dict] = []
    train_step = 0
    eval_step = 0
    for match in re.finditer(r"\{[^\n]*\}", log_text):
        try:
            item = ast.literal_eval(match.group(0))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if "loss" in item and "eval_loss" not in item:
            train_step += 10
            row = {"step": train_step}
            row.update({k: _to_float(v) for k, v in item.items()})
            train_rows.append(row)
        if "eval_loss" in item:
            eval_step += 100
            row = {"step": eval_step}
            row.update({k: _to_float(v) for k, v in item.items()})
            eval_rows.append(row)
    return train_rows, eval_rows


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return value


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row.keys()}, key=lambda k: (k != "step", k))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def plot(train_rows: list[dict], eval_rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 5.5))
    if train_rows:
        plt.plot([r["step"] for r in train_rows], [r["loss"] for r in train_rows], label="train loss", color="#2563eb")
    if eval_rows:
        plt.plot([r["step"] for r in eval_rows], [r["eval_loss"] for r in eval_rows], label="eval loss", color="#dc2626", marker="o")
    plt.title("Whisper Large-v3 Medical Fine-tuning Loss")
    plt.xlabel("Step")
    plt.ylabel("Loss")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "whisper_large_loss_curve.png", dpi=180)
    plt.close()

    plt.figure(figsize=(10, 5.5))
    if eval_rows:
        plt.plot([r["step"] for r in eval_rows], [r["eval_cer"] for r in eval_rows], label="eval CER", color="#7c3aed", marker="o")
        best = min(eval_rows, key=lambda r: r["eval_cer"])
        plt.scatter([best["step"]], [best["eval_cer"]], color="#16a34a", zorder=4, label=f"best step {best['step']}: {best['eval_cer']:.4f}")
    plt.title("Whisper Large-v3 Medical Fine-tuning CER")
    plt.xlabel("Step")
    plt.ylabel("CER")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "whisper_large_cer_curve.png", dpi=180)
    plt.close()


def main() -> None:
    if not LOG_PATH.exists():
        raise FileNotFoundError(f"Missing log file: {LOG_PATH}")
    train_rows, eval_rows = parse_metrics(LOG_PATH.read_text(encoding="utf-8", errors="ignore"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "whisper_large_train_metrics.csv", train_rows)
    write_csv(OUT_DIR / "whisper_large_eval_metrics.csv", eval_rows)
    plot(train_rows, eval_rows)
    print(f"train points: {len(train_rows)}")
    print(f"eval points: {len(eval_rows)}")
    if eval_rows:
        best = min(eval_rows, key=lambda r: r["eval_cer"])
        print(f"best eval CER: step={best['step']} cer={best['eval_cer']:.6f}")


if __name__ == "__main__":
    main()

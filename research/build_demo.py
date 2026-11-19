"""Embed research/results/report.json into the demo page: research/demo/index.html (self-contained, offline).

    python -m research.build_demo
"""
import json
from datetime import UTC, datetime
from pathlib import Path

from research.experiment import OUT

DEMO = Path("research/demo")


def main() -> None:
    rep = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    rep["built"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rep["provider"] = "NVIDIA Nemotron Ultra (nemotron-3-ultra-550b), temperature 0"
    data = json.dumps(rep, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (DEMO / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    (DEMO / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()

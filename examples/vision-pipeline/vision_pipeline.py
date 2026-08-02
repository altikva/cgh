# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Image pipeline in a batch job: inventory first (never
#              assume a diagram), then only the extractors the content
#              warrants. A logo costs one call; a diagram comes back as
#              markdown and Mermaid; identities read off the image are
#              pseudonymized before printing.
#              Requires: pip install cgh cgh-vision, a local Ollama
#              daemon, and: ollama pull qwen2.5vl:3b gemma3:4b

from __future__ import annotations

import secrets
import sys

from codegraph import sdk

SECRET = secrets.token_bytes(32)


def main(image: str) -> None:
    inv = sdk.image_inventory(image)
    print(f"content: {', '.join(inv['content'])}")
    print(f"summary: {inv['summary']}\n")

    if {"architecture_diagram", "flowchart"} & set(inv["content"]):
        ex = sdk.extract_diagram(image)
        for node in ex["nodes"]:
            for ident in node["identities"]:
                print(
                    "identity:", sdk.pseudonymize("pii.image_identity", ident, SECRET)
                )
        print(ex["markdown"])
    if "table" in inv["content"]:
        for table in sdk.extract_table(image):
            print(f"table: {table['columns']} x {len(table['rows'])} rows")
    if "chart" in inv["content"]:
        for chart in sdk.extract_chart(image):
            print(f"chart: {chart['type']} {chart['title']!r}: {chart['insight']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: vision_pipeline.py <image>")
    main(sys.argv[1])

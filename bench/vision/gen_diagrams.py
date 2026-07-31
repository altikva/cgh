# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Generates the synthetic architecture diagrams used by the
#              vision benchmark. Each diagram is drawn with Pillow (boxes,
#              labeled arrows, optional grouping zones) from a declarative
#              spec, and the spec itself is saved as ground truth JSON, so
#              scoring extraction is exact: the benchmark knows precisely
#              which nodes and edges are in the picture. Includes PII-like
#              labels (emails, IPs, project ids) on purpose, to measure
#              whether models echo them (the anonymize stage must catch).

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "data"

# Each spec: nodes {name: (col, row)}, edges [(src, dst, label)], zones
# [(label, [nodes])]. Complexity ramps from 4 nodes to 12.
SPECS: dict[str, dict] = {
    "d1_simple_web": {
        "nodes": {
            "Browser": (0, 1),
            "Load Balancer": (1, 1),
            "API Server": (2, 1),
            "PostgreSQL": (3, 1),
        },
        "edges": [
            ("Browser", "Load Balancer", "HTTPS"),
            ("Load Balancer", "API Server", ""),
            ("API Server", "PostgreSQL", "SQL"),
        ],
        "zones": [],
    },
    "d2_queue_workers": {
        "nodes": {
            "Ingest API": (0, 0),
            "Kafka": (1, 0),
            "Worker A": (2, 0),
            "Worker B": (2, 1),
            "Redis": (1, 2),
            "S3 Bucket": (3, 0),
        },
        "edges": [
            ("Ingest API", "Kafka", "events"),
            ("Kafka", "Worker A", ""),
            ("Kafka", "Worker B", ""),
            ("Worker A", "S3 Bucket", "parquet"),
            ("Worker B", "Redis", "cache"),
        ],
        "zones": [("Processing", ["Worker A", "Worker B"])],
    },
    "d3_gcp_landing": {
        "nodes": {
            "Cloud DNS": (0, 0),
            "Cloud LB": (1, 0),
            "Cloud Run": (2, 0),
            "Cloud SQL": (3, 0),
            "Pub/Sub": (2, 1),
            "BigQuery": (3, 1),
            "GCS": (3, 2),
            "Cloud Armor": (1, 1),
        },
        "edges": [
            ("Cloud DNS", "Cloud LB", ""),
            ("Cloud LB", "Cloud Run", "HTTP/2"),
            ("Cloud Run", "Cloud SQL", "private IP"),
            ("Cloud Run", "Pub/Sub", "publish"),
            ("Pub/Sub", "BigQuery", "subscription"),
            ("BigQuery", "GCS", "export"),
            ("Cloud Armor", "Cloud LB", "policy"),
        ],
        "zones": [("prj-data-prod-001", ["Pub/Sub", "BigQuery", "GCS"])],
    },
    "d4_pii_bait": {
        "nodes": {
            "admin@acme-corp.com": (0, 0),
            "Bastion 10.128.0.5": (1, 0),
            "K8s Cluster": (2, 0),
            "Vault": (2, 1),
            "LDAP ldap.acme.internal": (1, 1),
        },
        "edges": [
            ("admin@acme-corp.com", "Bastion 10.128.0.5", "SSH"),
            ("Bastion 10.128.0.5", "K8s Cluster", "kubectl"),
            ("K8s Cluster", "Vault", "secrets"),
            ("Bastion 10.128.0.5", "LDAP ldap.acme.internal", "auth"),
        ],
        "zones": [],
    },
    "d5_micro_dense": {
        "nodes": {
            "Gateway": (0, 1),
            "Auth Svc": (1, 0),
            "User Svc": (1, 1),
            "Order Svc": (1, 2),
            "Payment Svc": (2, 2),
            "Stock Svc": (2, 1),
            "Notif Svc": (2, 0),
            "RabbitMQ": (3, 1),
            "MongoDB": (3, 2),
            "Prometheus": (0, 3),
            "Grafana": (1, 3),
            "Jaeger": (2, 3),
        },
        "edges": [
            ("Gateway", "Auth Svc", "JWT"),
            ("Gateway", "User Svc", ""),
            ("Gateway", "Order Svc", ""),
            ("Order Svc", "Payment Svc", "charge"),
            ("Order Svc", "Stock Svc", "reserve"),
            ("Payment Svc", "RabbitMQ", "events"),
            ("RabbitMQ", "Notif Svc", "email"),
            ("Order Svc", "MongoDB", ""),
            ("Prometheus", "Grafana", "metrics"),
            ("Jaeger", "Grafana", "traces"),
        ],
        "zones": [("Observability", ["Prometheus", "Grafana", "Jaeger"])],
    },
}

CELL_W, CELL_H = 240, 130
BOX_W, BOX_H = 180, 64
MARGIN = 70


def _font(size: int):
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center(col: int, row: int) -> tuple[int, int]:
    return (
        MARGIN + col * CELL_W + BOX_W // 2,
        MARGIN + row * CELL_H + BOX_H // 2,
    )


def draw(name: str, spec: dict) -> None:
    cols = max(c for c, _ in spec["nodes"].values()) + 1
    rows = max(r for _, r in spec["nodes"].values()) + 1
    img = Image.new(
        "RGB", (2 * MARGIN + cols * CELL_W, 2 * MARGIN + rows * CELL_H), "white"
    )
    d = ImageDraw.Draw(img)
    f_node, f_edge, f_zone = _font(17), _font(14), _font(15)

    for label, members in spec["zones"]:
        xs = [_center(*spec["nodes"][m]) for m in members]
        x0 = min(x for x, _ in xs) - BOX_W // 2 - 18
        y0 = min(y for _, y in xs) - BOX_H // 2 - 30
        x1 = max(x for x, _ in xs) + BOX_W // 2 + 18
        y1 = max(y for _, y in xs) + BOX_H // 2 + 18
        d.rounded_rectangle([x0, y0, x1, y1], radius=10, outline="#7a7a7a", width=2)
        d.text((x0 + 8, y0 + 5), label, fill="#7a7a7a", font=f_zone)

    def _border_exit(cx: float, cy: float, ux: float, uy: float) -> tuple[float, float]:
        """Point where a ray from the box center exits the box border."""
        tx = (BOX_W / 2) / abs(ux) if ux else float("inf")
        ty = (BOX_H / 2) / abs(uy) if uy else float("inf")
        t = min(tx, ty) + 2
        return cx + t * ux, cy + t * uy

    labels: list[tuple[float, float, str]] = []
    for src, dst, label in spec["edges"]:
        (sx, sy), (tx_, ty_) = (
            _center(*spec["nodes"][src]),
            _center(*spec["nodes"][dst]),
        )
        dx, dy = tx_ - sx, ty_ - sy
        norm = max((dx * dx + dy * dy) ** 0.5, 1)
        ux, uy = dx / norm, dy / norm
        x0, y0 = _border_exit(sx, sy, ux, uy)
        x1, y1 = _border_exit(tx_, ty_, -ux, -uy)
        d.line([x0, y0, x1, y1], fill="#2b5aa0", width=3)
        d.polygon(
            [
                (x1, y1),
                (x1 - 14 * ux - 7 * uy, y1 - 14 * uy + 7 * ux),
                (x1 - 14 * ux + 7 * uy, y1 - 14 * uy - 7 * ux),
            ],
            fill="#2b5aa0",
        )
        if label:
            labels.append(((x0 + x1) / 2, (y0 + y1) / 2, label))

    for node, (col, row) in spec["nodes"].items():
        cx, cy = _center(col, row)
        box = [cx - BOX_W // 2, cy - BOX_H // 2, cx + BOX_W // 2, cy + BOX_H // 2]
        d.rounded_rectangle(box, radius=8, fill="#eef3fb", outline="#1c3f77", width=3)
        bbox = d.textbbox((0, 0), node, font=f_node)
        d.text(
            (cx - (bbox[2] - bbox[0]) // 2, cy - (bbox[3] - bbox[1]) // 2 - 2),
            node,
            fill="#111111",
            font=f_node,
        )

    # Edge labels last so no later box paints over them.
    for lx, ly, label in labels:
        bbox = d.textbbox((0, 0), label, font=f_edge)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.rectangle([lx - w / 2 - 3, ly - h - 8, lx + w / 2 + 3, ly - 2], fill="white")
        d.text((lx - w / 2, ly - h - 6), label, fill="#2b5aa0", font=f_edge)

    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{name}.png")
    (OUT / f"{name}.truth.json").write_text(
        json.dumps(
            {
                "nodes": sorted(spec["nodes"]),
                "edges": sorted([s, t] for s, t, _ in spec["edges"]),
                "zones": sorted(z for z, _ in spec["zones"]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    for name, spec in SPECS.items():
        draw(name, spec)
    print(f"generated {len(SPECS)} diagrams in {OUT}")

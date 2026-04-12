"""Shared test fixtures for codegraph."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def sample_python(tmp_path: Path) -> Path:
    """Create a sample Python file for parser tests."""
    f = tmp_path / "sample.py"
    f.write_text(
        textwrap.dedent('''\
        """Sample module docstring."""

        import os
        from pathlib import Path

        class BaseHandler:
            """Base handler class."""

            def handle(self, request):
                """Handle a request."""
                return self.process(request)

            def process(self, request):
                pass

        class DonationHandler(BaseHandler):
            """Handles donation logic."""

            def handle(self, request):
                validate(request)
                return super().handle(request)

        def validate(data):
            """Validate input data."""
            if not data:
                raise ValueError("empty")
            return True

        def main():
            handler = DonationHandler()
            handler.handle({"amount": 100})
    ''')
    )
    return f


@pytest.fixture
def sample_typescript(tmp_path: Path) -> Path:
    """Create a sample TypeScript file for parser tests."""
    f = tmp_path / "sample.ts"
    f.write_text(
        textwrap.dedent("""\
        import { ref } from 'vue'
        import type { Donor } from './types'

        interface DonorProfile {
            name: string
            email: string
        }

        class DonorService {
            private donors: Donor[] = []

            async fetchDonor(id: string): Promise<Donor> {
                return this.donors.find(d => d.id === id)
            }
        }

        export function formatAmount(amount: number, currency: string): string {
            return `${amount} ${currency}`
        }

        const handler = (event: Event) => {
            console.log(event)
        }
    """)
    )
    return f


@pytest.fixture
def sample_terraform(tmp_path: Path) -> Path:
    """Create a sample Terraform file for parser tests."""
    f = tmp_path / "main.tf"
    f.write_text(
        textwrap.dedent("""\
        variable "project_id" {
          type = string
        }

        variable "region" {
          type    = string
          default = "europe-west1"
        }

        resource "google_storage_bucket" "main" {
          name     = "${var.project_id}-storage"
          location = var.region
        }

        resource "google_storage_bucket_iam_member" "public" {
          bucket = google_storage_bucket.main.name
          role   = "roles/storage.objectViewer"
          member = "allUsers"
        }

        output "bucket_url" {
          value = google_storage_bucket.main.url
        }
    """)
    )
    return f


@pytest.fixture
def sample_markdown(tmp_path: Path) -> Path:
    """Create a sample Markdown file for parser tests."""
    f = tmp_path / "README.md"
    f.write_text(
        textwrap.dedent("""\
        # Project Overview

        This is the main project documentation.

        ## Architecture

        The system uses a 4-layer architecture:

        - Router → Handler → Manager → Service

        See [setup guide](./SETUP.md) for details.

        ### Components

        The `DonationHandler` processes all donations.

        ```python
        handler = DonationHandler()
        handler.handle(request)
        ```

        ## API Reference

        See the [API docs](./api/openapi.json).
    """)
    )
    return f


@pytest.fixture
def sample_repo(tmp_path: Path, sample_python, sample_typescript, sample_terraform, sample_markdown) -> Path:
    """Create a minimal repo with all supported file types."""
    # Files are already in tmp_path from the fixtures
    return tmp_path

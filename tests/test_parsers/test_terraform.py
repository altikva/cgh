"""Tests for the Terraform parser."""

from codegraph.parsers import get_parser
from codegraph.parsers.base import FileIndex


class TestTerraformParser:
    def test_parser_exists(self):
        parser = get_parser(".tf")
        assert parser is not None
        assert parser.lang == "terraform"

    def test_parse_resources(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)
        assert isinstance(idx, FileIndex)

        resources = [r for r in idx.resources if r.kind == "resource"]
        resource_names = [r.name for r in resources]
        assert "main" in resource_names
        assert "public" in resource_names

    def test_parse_variables(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)

        variables = [r for r in idx.resources if r.kind == "variable"]
        var_names = [v.name for v in variables]
        assert "project_id" in var_names
        assert "region" in var_names

    def test_parse_outputs(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)

        outputs = [r for r in idx.resources if r.kind == "output"]
        output_names = [o.name for o in outputs]
        assert "bucket_url" in output_names

    def test_resource_types(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)

        bucket = next(r for r in idx.resources if r.name == "main" and r.kind == "resource")
        assert bucket.type == "google_storage_bucket"

    def test_line_numbers(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)

        for res in idx.resources:
            assert res.start_line > 0
            assert res.file_path == str(sample_terraform)

    def test_resource_ids(self, sample_terraform):
        parser = get_parser(".tf")
        idx = parser.parse(sample_terraform)

        for res in idx.resources:
            assert "::" in res.id
            assert str(sample_terraform) in res.id

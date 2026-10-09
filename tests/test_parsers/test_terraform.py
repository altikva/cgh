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

        bucket = next(
            r for r in idx.resources if r.name == "main" and r.kind == "resource"
        )
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


# ---------------------------------------------------------------------------
# Addressed blocks and references (tree-sitter-hcl)
# ---------------------------------------------------------------------------

HCL = """\
# a comment naming var.in_comment and google_x_y.in_comment
terraform {
  required_version = ">= 1.6"
}

provider "google" {
  project = var.project_id
}

locals {
  prefix = "app-${var.env}-{literal}"
  names  = [for s in var.services : s.name if s.enabled]
  banner = <<-EOT
    built for ${local.prefix} in ${data.google_project.p.number}
    %{ for r in var.regions }${r} %{ endfor }
  EOT
}

data "google_project" "p" {}

module "net" {
  source = "./modules/net"
  region = var.region
  name   = local.prefix
  count  = 1
}

module "remote" {
  source  = "git::https://example.com/mods.git//x?ref=v1"
  project = var.project_id
}

resource "google_compute_instance" "vm" {
  name = "vm-{not-a-ref}"
  network = module.net[0].network_id
  // google_x_y.in_line_comment
  dynamic "disk" {
    for_each = var.disks
    content {
      size = disk.value.size
      kms  = google_kms_crypto_key.key.id
    }
  }
  lifecycle {
    ignore_changes = [labels]
  }
  depends_on = [google_project_service.apis]
}

variable "region" {
  type        = string
  description = "Where it runs"
  validation {
    condition     = length(var.region) > 0
    error_message = "Not empty: ${var.region}."
  }
}

output "vm_ids" {
  value = google_compute_instance.vm[*].id
}
"""


def _parse(tmp_path, text, name="main.tf"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return get_parser(".tf").parse(p)


def _by_address(idx):
    return {r.address: r for r in idx.resources}


class TestTerraformBlocks:
    def test_every_block_type_is_addressed(self, tmp_path):
        blocks = _by_address(_parse(tmp_path, HCL))
        assert {
            "provider.google",
            "local.prefix",
            "local.names",
            "local.banner",
            "data.google_project.p",
            "module.net",
            "module.net.region",
            "module.net.name",
            "module.remote",
            "module.remote.project",
            "google_compute_instance.vm",
            "var.region",
            "output.vm_ids",
        } == set(blocks)
        kinds = {a: r.kind for a, r in blocks.items()}
        assert kinds["data.google_project.p"] == "data"
        assert kinds["module.net"] == "module"
        assert kinds["local.prefix"] == "local"
        # Existing kinds and names keep working.
        assert kinds["google_compute_instance.vm"] == "resource"
        assert blocks["google_compute_instance.vm"].name == "vm"
        assert blocks["google_compute_instance.vm"].type == "google_compute_instance"
        assert blocks["var.region"].name == "region"

    def test_ids_and_line_ranges(self, tmp_path):
        idx = _parse(tmp_path, HCL)
        blocks = _by_address(idx)
        vm = blocks["google_compute_instance.vm"]
        assert vm.id == f"{idx.path}::google_compute_instance.vm"
        lines = HCL.splitlines()
        assert lines[vm.start_line - 1].startswith('resource "google_compute_instance"')
        assert lines[vm.end_line - 1] == "}"
        assert vm.end_line - vm.start_line > 10
        # A heredoc local spans its whole body.
        banner = blocks["local.banner"]
        assert lines[banner.end_line - 1].strip() == "EOT"

    def test_references_in_nested_blocks_strings_and_heredocs(self, tmp_path):
        blocks = _by_address(_parse(tmp_path, HCL))
        vm = blocks["google_compute_instance.vm"]
        assert "module.net.network_id" in vm.refs
        assert "google_kms_crypto_key.key" in vm.refs  # inside dynamic/content
        assert "google_project_service.apis" in vm.refs  # depends_on
        assert "var.disks" in vm.refs
        banner = blocks["local.banner"]
        assert {"local.prefix", "data.google_project.p", "var.regions"} <= set(
            banner.refs
        )
        assert blocks["local.prefix"].refs == ["var.env"]

    def test_no_reference_from_comments_literals_or_iterators(self, tmp_path):
        idx = _parse(tmp_path, HCL)
        every = {ref for r in idx.resources for ref in r.refs}
        assert not any("in_comment" in r or "in_line_comment" in r for r in every)
        # for-loop and dynamic-block iterators are not block addresses.
        assert not any(r.startswith(("s.", "disk.", "r.")) for r in every)
        assert "var.services" in _by_address(idx)["local.names"].refs

    def test_self_reference_is_dropped(self, tmp_path):
        region = _by_address(_parse(tmp_path, HCL))["var.region"]
        assert "var.region" not in region.refs

    def test_module_source_and_inputs(self, tmp_path):
        blocks = _by_address(_parse(tmp_path, HCL))
        net = blocks["module.net"]
        assert net.source == "./modules/net"
        assert net.inputs == ["region", "name"]  # meta-arguments left out
        assert {"var.region", "local.prefix"} <= set(net.refs)
        assert blocks["module.remote"].source.startswith("git::")

    def test_module_arguments_are_entries_of_their_own(self, tmp_path):
        idx = _parse(tmp_path, HCL)
        blocks = _by_address(idx)
        region = blocks["module.net.region"]
        assert (region.kind, region.name, region.type) == (
            "module_arg",
            "region",
            "module.net",
        )
        assert region.start_line == region.end_line
        assert HCL.splitlines()[region.start_line - 1] == "  region = var.region"
        assert region.inputs == ["region"]
        assert region.source == "./modules/net"
        # Expression references stay on the module block, not on the input.
        assert region.refs == []
        assert region.docstring == "module.net.region = var.region"
        # Meta-arguments are not inputs.
        assert "module.net.count" not in blocks
        assert "module.net.source" not in blocks

    def test_moved_import_and_removed_blocks(self, tmp_path):
        text = (
            "moved {\n"
            "  from = module.vpc.google_compute_network.vpc\n"
            "  to   = module.vpc.google_compute_network.this[0]\n"
            "}\n"
            "moved {\n  from = module.tasks.random_id.s\n  to = random_id.s\n}\n"
            'import {\n  to = google_kms_key_ring.ring["a"]\n  id = "x"\n}\n'
            "removed {\n  from = data.google_project.old\n"
            "  lifecycle {\n    destroy = false\n  }\n}\n"
            "moved {\n  from = 1\n}\n"
        )
        blocks = _by_address(_parse(tmp_path, text))
        assert set(blocks) == {
            "moved.module.vpc.google_compute_network.this",
            "moved.random_id.s",
            "import.google_kms_key_ring.ring",
            "removed.data.google_project.old",
        }
        vpc = blocks["moved.module.vpc.google_compute_network.this"]
        assert vpc.kind == "moved" and (vpc.start_line, vpc.end_line) == (1, 4)
        assert vpc.refs == [
            "module.vpc.google_compute_network.vpc",
            "module.vpc.google_compute_network.this",
        ]
        assert blocks["moved.random_id.s"].refs == [
            "module.tasks.random_id.s",
            "random_id.s",
        ]
        assert blocks["import.google_kms_key_ring.ring"].refs == [
            "google_kms_key_ring.ring"
        ]
        assert blocks["removed.data.google_project.old"].refs == [
            "data.google_project.old"
        ]

    def test_splat_reference(self, tmp_path):
        out = _by_address(_parse(tmp_path, HCL))["output.vm_ids"]
        assert out.refs == ["google_compute_instance.vm"]

    def test_summary_for_text_search(self, tmp_path):
        blocks = _by_address(_parse(tmp_path, HCL))
        doc = blocks["google_compute_instance.vm"].docstring
        assert doc.startswith('resource "google_compute_instance" "vm"')
        assert "network=module.net[0].network_id" in doc
        assert "blocks: dynamic disk, lifecycle" in doc
        assert "Where it runs" in blocks["var.region"].docstring

    def test_braces_inside_strings_do_not_break_blocks(self, tmp_path):
        text = (
            'resource "a_b" "one" {\n  x = "}{ ${var.v} }"\n}\n'
            'resource "a_b" "two" {\n  y = a_b.one.id\n}\n'
        )
        blocks = _by_address(_parse(tmp_path, text))
        assert blocks["a_b.one"].end_line == 3
        assert blocks["a_b.two"].refs == ["a_b.one"]

    def test_tfvars_entries_reference_their_variable(self, tmp_path):
        p = tmp_path / "terraform.tfvars"
        p.write_text(
            '# env\nregion = "europe-west1"\nlabels = {\n  team = "infra"\n}\n',
            encoding="utf-8",
        )
        idx = get_parser(".tfvars").parse(p)
        by_name = {r.name: r for r in idx.resources}
        assert set(by_name) == {"region", "labels"}
        assert by_name["region"].kind == "tfvars"
        assert by_name["region"].refs == ["var.region"]
        assert by_name["region"].docstring == 'region = "europe-west1"'
        assert (by_name["labels"].start_line, by_name["labels"].end_line) == (3, 5)

    def test_broken_file_still_yields_what_parses(self, tmp_path):
        text = 'resource "a_b" "ok" {\n  x = var.v\n}\n\nresource "a_b" "bad" {\n'
        blocks = _by_address(_parse(tmp_path, text))
        assert "a_b.ok" in blocks


def test_contract_yaml_alias_bomb_stays_bounded(tmp_path):
    """YAML aliases can expand a small file into huge shared structures; the
    contracts walk must stay capped in sections and in time."""
    import time

    from codegraph.parsers.config_data import _CONTRACT_MAX_SECTIONS, YamlParser

    lines = ["a: &a {" + ", ".join(f"k{i}: v" for i in range(200)) + "}"]
    prev = "a"
    for n in "bcdefghij":
        lines.append(
            f"{n}: &{n} {{" + ", ".join(f"x{i}: *{prev}" for i in range(200)) + "}"
        )
        prev = n
    lines.append("top: {" + ", ".join(f"y{i}: *{prev}" for i in range(200)) + "}")
    path = tmp_path / "contracts" / "bomb.yaml"
    path.parent.mkdir()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    start = time.monotonic()
    idx = YamlParser().parse(path)
    elapsed = time.monotonic() - start
    assert len(idx.sections) <= _CONTRACT_MAX_SECTIONS
    assert elapsed < 5, f"took {elapsed:.1f} s"
    assert all(len(s.body_preview) <= 400 for s in idx.sections)

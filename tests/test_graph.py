import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("wiki_graph", Path(__file__).resolve().parents[1] / "tools/wiki.py")
wiki = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiki)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        self.run.mkdir()
        self.state = wiki.initial_state("test-only")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cannot_skip_user_gate(self):
        with self.assertRaisesRegex(ValueError, "Dependencies not passed"):
            wiki.begin(self.run, self.state, "G5")
        with self.assertRaisesRegex(ValueError, "Human gates"):
            wiki.begin(self.run, self.state, "H1")

    def test_rejected_gate_resubmission_keeps_downstream_closed(self):
        for sid in ["P01", "P02", "P03", "P04", "P05"]:
            self.state["nodes"]["G4:" + sid].update(status="passed", outputs={})
        rejection = self.run / "rejected.json"
        wiki.write_json(rejection, {"decision": "reject", "fixture": True})
        self.state["nodes"]["H1"].update(status="stale", outputs={str(rejection): wiki.digest(rejection)})
        draft = self.run / "revision.md"
        draft.write_bytes(b"test fixture only\n")
        package = self.run / "package.json"
        wiki.write_json(package, {"revision_id": "test-only", "outputs": [str(draft)]})
        with patch.object(wiki, "allowed_artifact", side_effect=lambda p: Path(p)):
            wiki.submit_review(self.run, self.state, "H1", package)
        self.assertEqual(self.state["nodes"]["H1"]["status"], "awaiting_review")
        self.assertIn(str(rejection), self.state["nodes"]["H1"]["outputs"])
        self.assertEqual(wiki.read_json(rejection)["decision"], "reject")
        with self.assertRaisesRegex(ValueError, "Dependencies not passed"):
            wiki.begin(self.run, self.state, "G5")

    def test_resubmission_requires_completed_dependencies(self):
        with self.assertRaisesRegex(ValueError, "not ready"):
            wiki.submit_review(self.run, self.state, "H1", self.run / "not-read.json")

    def test_equation_labels_preserve_decimal_suffix_observed_in_P01(self):
        text = "formula (3-4.1), reference (3-6.3), wide parentheses （2－8）"
        self.assertEqual(wiki.equation_labels(text), ["3-4.1", "3-6.3", "2-8"])

    def test_math_is_not_misread_as_markdown_link(self):
        text = "$R=[1/(q n_s mu)](L/W)$ and [real](concept.md)"
        self.assertEqual(wiki.markdown_links(text), [("", "concept.md")])

    def test_json_writer_preserves_lf_bytes_across_platforms(self):
        path = self.run / "portable.json"
        wiki.write_json(path, {"scope": "fixture", "items": [1, 2]})
        self.assertNotIn(b"\r\n", path.read_bytes())
        self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_failure_routes_to_actual_source_and_invalidates_dependents(self):
        self.state["nodes"]["G0"]["status"] = "passed"
        self.state["nodes"]["G1:P01"]["status"] = "passed"
        wiki.begin(self.run, self.state, "G2:P01")
        target = wiki.fail_node(self.run, self.state, "G2:P01", "unreadable_page")
        self.assertEqual(target, "G1:P01")
        self.assertEqual(self.state["nodes"]["G1:P01"]["status"], "stale")
        self.assertEqual(self.state["nodes"]["G2:P01"]["status"], "stale")
        self.assertEqual(self.state["nodes"]["G2:P01"]["attempts"], 1)

    def test_attempt_limit_survives_invalidation(self):
        for _ in range(3):
            wiki.begin(self.run, self.state, "G0")
            wiki.invalidate(self.state, "G0", "test_only")
        with self.assertRaisesRegex(ValueError, "Attempt limit"):
            wiki.begin(self.run, self.state, "G0")
        self.assertEqual(self.state["nodes"]["G0"]["status"], "blocked")

    def test_run_repair_budget(self):
        self.state["nodes"]["G0"].update(status="failed", attempts=1)
        self.state["repairs"] = wiki.graph()["max_automatic_repairs_per_run"]
        with self.assertRaisesRegex(ValueError, "repair limit"):
            wiki.begin(self.run, self.state, "G0")

    def test_changed_output_invalidates_only_dependent_branch(self):
        output = self.run / "evidence.json"
        output.write_text("first", encoding="utf-8")
        self.state["nodes"]["G2:P01"].update(status="passed", outputs={str(output): wiki.digest(output)})
        self.state["nodes"]["G3:P01"]["status"] = "passed"
        self.state["nodes"]["G4:P01"]["status"] = "passed"
        self.state["nodes"]["H1"]["status"] = "passed"
        self.state["nodes"]["G2:P02"]["status"] = "passed"
        output.write_text("corrected", encoding="utf-8")
        wiki.refresh(self.run, self.state)
        for node in ["G2:P01", "G3:P01", "G4:P01", "H1"]:
            self.assertEqual(self.state["nodes"][node]["status"], "stale")
        self.assertEqual(self.state["nodes"]["G2:P02"]["status"], "passed")

    def test_page_range_and_source_identity(self):
        result = {"checks": {key: "passed" for key in wiki.definitions()["G2:P01"]["checks"]},
                  "scope": "test only", "open_items": [], "outputs": ["not-read-by-structure-validation"],
                  "evidence": [{"source_id": "P01", "pdf_page": 108, "claim": "fixture", "basis": "paper", "status": "pending"}]}
        with self.assertRaisesRegex(ValueError, "Invalid source or PDF page"):
            wiki.validate_result("G2:P01", result)
        result["evidence"][0].update(source_id="P02", pdf_page=1)
        with self.assertRaisesRegex(ValueError, "match node source"):
            wiki.validate_result("G2:P01", result)

    def test_private_root_inside_repo_is_rejected(self):
        with patch.dict(os.environ, {"GAN_WIKI_PRIVATE_ROOT": str(wiki.REPO / "hidden-private")}, clear=False):
            with self.assertRaisesRegex(ValueError, "outside"):
                wiki.private_root(wiki.REPO)

    def test_public_guard_catches_unlisted_pdf_and_escaping_link(self):
        repo = Path(self.tmp.name) / "public"
        (repo / "publication").mkdir(parents=True)
        page = repo / "README.md"
        page.write_text("[bad](../private/source.txt)\n", encoding="utf-8", newline="\n")
        wiki.write_json(repo / "publication/public-files.json", {"files": {"README.md": {"sha256": wiki.digest(page)}}})
        (repo / "secret.pdf").write_bytes(b"%PDF-fixture")
        errors = wiki.public_errors(repo)
        self.assertTrue(any("Unlisted" in e for e in errors))
        self.assertTrue(any("Forbidden" in e for e in errors))
        self.assertTrue(any("escaping link" in e for e in errors))

    def test_privacy_scanner_accepts_its_definition_but_rejects_actual_path(self):
        repo = Path(self.tmp.name) / "public"
        (repo / "publication").mkdir(parents=True)
        code = repo / "checker.py"
        code.write_text((wiki.REPO / "tools/wiki.py").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        wiki.write_json(repo / "publication/public-files.json", {"files": {"checker.py": {"sha256": wiki.digest(code)}}})
        self.assertEqual(wiki.public_errors(repo), [])
        code.write_text("/" + "home" + "/" + "fixture-person" + "/document", encoding="utf-8")
        self.assertTrue(any("Potential private path" in e for e in wiki.public_errors(repo)))

    def test_export_discards_private_scope_paths_and_unknown_reason(self):
        fake_repo = Path(self.tmp.name) / "public"
        fake_repo.mkdir()
        (fake_repo / "workflow").mkdir()
        wiki.write_json(fake_repo / "workflow/graph.json", wiki.graph())
        self.state["nodes"]["G0"].update(scope="private source excerpt", outputs={"private-input": "hash"})
        wiki.event(self.run, self.state, "G0", "failed", reason_code="private source paragraph", detail="secret source extract")
        with patch.object(wiki, "REPO", fake_repo):
            wiki.export_run(self.run, self.state)
        public_text = "".join(path.read_text(encoding="utf-8") for path in (fake_repo / "records").rglob("*") if path.is_file())
        for sensitive in ["private source excerpt", "private-input", "private source paragraph", "secret source extract"]:
            self.assertNotIn(sensitive, public_text)
        self.assertIn('"other"', public_text)
        for path in (fake_repo / "records").rglob("*"):
            if path.is_file():
                self.assertNotIn(b"\r\n", path.read_bytes())

    def test_manifest_requires_explicit_origin_registration(self):
        fake_repo = Path(self.tmp.name) / "public"
        (fake_repo / "publication").mkdir(parents=True)
        (fake_repo / "sources").mkdir()
        wiki.write_json(fake_repo / "sources/catalog.json", {"sources": []})
        wiki.write_json(fake_repo / "publication/registrations.json", {"files": {}})
        (fake_repo / "unreviewed.md").write_text("fixture", encoding="utf-8")
        with patch.object(wiki, "REPO", fake_repo):
            with self.assertRaisesRegex(ValueError, "Register origin"):
                wiki.update_manifest()

    def test_registered_public_package_builds_and_detects_later_tampering(self):
        fake_repo = Path(self.tmp.name) / "public"
        (fake_repo / "publication").mkdir(parents=True)
        (fake_repo / "sources").mkdir()
        wiki.write_json(fake_repo / "sources/catalog.json", {"sources": []})
        (fake_repo / "README.md").write_text("Project scaffold\n", encoding="utf-8", newline="\n")
        names = ["README.md", "sources/catalog.json", "publication/registrations.json"]
        wiki.write_json(fake_repo / "publication/registrations.json", {"files": {
            name: {"origin": "test_project_authored", "release": "eligible_project_authored", "basis": "test fixture"}
            for name in names}})
        with patch.object(wiki, "REPO", fake_repo):
            wiki.update_manifest()
        self.assertEqual(wiki.public_errors(fake_repo), [])
        (fake_repo / "README.md").write_text("Changed after approval\n", encoding="utf-8", newline="\n")
        self.assertTrue(any("Changed file" in e for e in wiki.public_errors(fake_repo)))


if __name__ == "__main__":
    unittest.main()

"""Tests for the official manual library.

These run entirely offline: they check the shipped catalogue and its logic, not
the manufacturer web servers.
"""

from __future__ import annotations

import unittest

from korg_binary_analyzer.manuals import (
    brands,
    catalogue_size,
    deep_reference_docs,
    families,
    human_size,
    load_catalog,
    manuals_for_model,
    parse_size,
    plan_download,
    products,
    safe_filename,
    search,
)


class CatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog()
        cls.products = products(cls.catalog)

    def test_catalogue_ships_and_is_populated(self) -> None:
        self.assertGreaterEqual(len(self.products), 40)
        self.assertGreaterEqual(catalogue_size(self.catalog), 500 * 1024 * 1024)

    def test_all_three_brands_present(self) -> None:
        self.assertEqual(brands(self.catalog), ["Korg", "Roland", "Yamaha"])

    def test_every_entry_is_an_official_https_link(self) -> None:
        allowed = ("https://cdn.korg.com/", "https://www.korg.com/", "https://usa.yamaha.com/",
                   "https://www.roland.com/", "https://static.roland.com/")
        for name, item in self.products.items():
            for doc in item["docs"]:
                self.assertTrue(
                    doc["direct"].startswith(allowed),
                    f"{name}: unexpected host in {doc['direct']}",
                )
                self.assertTrue(doc["direct"].lower().endswith(".pdf") or ".pdf" in doc["direct"].lower())

    def test_no_duplicate_urls_within_a_model(self) -> None:
        for name, item in self.products.items():
            urls = [doc["direct"] for doc in item["docs"]]
            self.assertEqual(len(urls), len(set(urls)), f"{name} repeats a document")

    def test_no_non_english_documents_shipped(self) -> None:
        import re as _re
        for name, item in self.products.items():
            for doc in item["docs"]:
                blob = f"{doc.get('filename', '')} {doc.get('doc', '')}".casefold()
                self.assertIsNone(
                    _re.search(r"\b(deutsch|français|español|italiano|chinese|turkish)\b", blob),
                    f"{name}: non-English document leaked through: {doc.get('filename')}",
                )

    def test_key_models_are_documented(self) -> None:
        for model in ("Pa5X", "Pa4X", "Pa3X", "Pa800", "Pa600", "Pa1000", "Pa700"):
            self.assertIn(model, self.products, f"{model} missing from the catalogue")
        for model in ("Genos2", "Genos", "PSR-SX900", "Tyros5"):
            self.assertIn(model, self.products, f"{model} missing from the catalogue")
        for model in ("E-A7", "BK-5", "BK-3", "G-70", "BK-7M", "PRELUDE"):
            self.assertIn(model, self.products, f"{model} missing from the catalogue")

    def test_reference_grade_documentation_for_every_brand(self) -> None:
        # Korg's user manual is the deep document; for Yamaha it is the
        # Reference Manual. The catalogue records that difference per entry.
        for model in ("Pa5X", "Pa4X", "Pa3X", "Pa1000", "Pa700"):
            docs = deep_reference_docs(self.products[model])
            self.assertTrue(docs, f"{model} has no reference-grade documentation")
        for model in ("Genos2", "Genos", "PSR-SX900", "Tyros5"):
            docs = deep_reference_docs(self.products[model])
            self.assertTrue(
                any("Reference Manual" == d["doc"] for d in docs),
                f"{model} is missing its Reference Manual",
            )
        for model in ("E-A7", "BK-5", "BK-3", "G-70"):
            self.assertTrue(deep_reference_docs(self.products[model]), model)

    def test_every_document_declares_its_depth(self) -> None:
        for name, item in self.products.items():
            for doc in item["docs"]:
                self.assertIn(doc.get("depth"), {"reference", "guide"}, f"{name}/{doc.get('doc')}")

    def test_software_manuals_are_not_counted_as_reference(self) -> None:
        for name, item in self.products.items():
            for doc in item["docs"]:
                if str(doc.get("doc", "")).startswith("Software:"):
                    self.assertEqual(doc.get("depth"), "guide", f"{name}/{doc['doc']}")

    def test_container_era_is_recorded_for_every_korg_model(self) -> None:
        for name, item in self.products.items():
            if item["brand"] == "Korg":
                self.assertTrue(item.get("container_era"), f"{name} has no format note")


class HelperTests(unittest.TestCase):
    def test_parse_size_handles_the_catalogue_formats(self) -> None:
        self.assertEqual(parse_size("78.4MB"), int(78.4 * 1024 ** 2))
        self.assertEqual(parse_size("248.5KB"), int(248.5 * 1024))
        self.assertEqual(parse_size("3.3MB"), int(3.3 * 1024 ** 2))
        self.assertEqual(parse_size(None), 0)
        self.assertEqual(parse_size("weird"), 0)

    def test_human_size_round_trips(self) -> None:
        self.assertEqual(human_size(int(78.4 * 1024 ** 2)), "78.4 MB")
        self.assertEqual(human_size(0), "-")

    def test_model_lookup_prefers_the_longest_match(self) -> None:
        hits = manuals_for_model("PA3XLE_User_Manual")
        self.assertTrue(hits)
        self.assertIn("Le", hits[0][0])

    def test_model_lookup_ignores_punctuation(self) -> None:
        self.assertTrue(manuals_for_model("pa-4x"))
        self.assertTrue(manuals_for_model("PSR_SX900"))

    def test_search_finds_models_and_document_types(self) -> None:
        self.assertTrue(any("Pa5X" in name for name, _ in search("pa5x")))
        self.assertTrue(any("Genos" in name for name, _ in search("genos")))
        self.assertTrue(any("BK-5" in name for name, _ in search("bk-5")))

    def test_families_group_variants(self) -> None:
        grouped = families("Korg")
        self.assertIn("Pa3X", grouped)
        self.assertIn("Pa3X Le", grouped["Pa3X"])

    def test_safe_filename_strips_path_traversal(self) -> None:
        self.assertNotIn("/", safe_filename("../../evil.pdf"))
        self.assertNotIn("\\", safe_filename("..\\..\\evil.pdf"))
        self.assertTrue(safe_filename("../../evil.pdf").endswith(".pdf"))

    def test_plan_download_is_filterable(self) -> None:
        korg = plan_download(brand="Korg")
        self.assertTrue(korg)
        self.assertTrue(all(item["brand"] == "Korg" for item in korg))
        pa5x = plan_download(model="Pa5X")
        self.assertTrue(all("pa5x" in item["model"].casefold() for item in pa5x))
        deep = plan_download(brand="Korg", deep_only=True)
        self.assertTrue(deep)
        self.assertTrue(all(item["depth"] == "reference" for item in deep))

    def test_plan_download_reports_sizes(self) -> None:
        plan = plan_download(model="Pa5X")
        self.assertTrue(any(item["size_bytes"] > 1024 for item in plan))


class ReportIntegrationTests(unittest.TestCase):
    def test_report_names_the_matching_model(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from korg_binary_analyzer.decoder_engine import DecoderRegistry
        from korg_binary_analyzer.scanner import analyze_file
        from korg_binary_analyzer.selftest import build_synthetic_container

        with TemporaryDirectory() as folder:
            root = Path(folder) / "PA3X_BANK"
            root.mkdir()
            source = root / "PA3X_USER01.STY"
            source.write_bytes(build_synthetic_container())
            report = analyze_file(source, Path(folder) / "ws")
            manuals = report.get("manuals", {})
            self.assertEqual(manuals.get("status"), "MATCHED")
            matched = [item["model"] for item in manuals["matches"]]
            self.assertIn("Pa3X", matched)
            self.assertTrue(manuals["matches"][0]["documents"])

    def test_unmatched_file_offers_nothing(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from korg_binary_analyzer.scanner import analyze_file

        with TemporaryDirectory() as folder:
            source = Path(folder) / "random_blob.bin"
            source.write_bytes(b"\x00\x01\x02\x03" * 2048)
            report = analyze_file(source, Path(folder) / "ws")
            manuals = report.get("manuals", {})
            self.assertEqual(manuals.get("status"), "NO_MODEL_MATCH")
            self.assertEqual(manuals["matches"], [])


if __name__ == "__main__":
    unittest.main()

"""Tests for the local Ollama client and the grounded assistant layer.

Nothing here needs a real Ollama installation or a downloaded model: a tiny
in-process HTTP server speaks the same API, and the "model" it provides is a
deterministic echo that reports which facts it received.  That lets the tests
prove the evidence actually reaches the model instead of merely assuming it.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from korg_binary_analyzer.ai import (
    QUICK_PROMPTS,
    SYSTEM_PROMPT,
    _compact_report,
    build_messages,
    report_summary_line,
)
from korg_binary_analyzer.ollama_client import (
    MODEL_PREFERENCE,
    ModelInfo,
    OllamaClient,
    OllamaError,
    normalize_host,
    suggest_model,
)

FAKE_MODELS = [
    {"name": "qwen2.5:3b", "size": 1926311936, "details": {
        "family": "qwen2", "parameter_size": "3.1B", "quantization_level": "Q4_K_M"}},
    {"name": "qwen2.5:7b", "size": 4683087332, "details": {
        "family": "qwen2", "parameter_size": "7.6B", "quantization_level": "Q4_K_M"}},
]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    received: list[dict] = []

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ndjson(self, events: list[dict]) -> None:
        body = "".join(json.dumps(event) + "\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/version":
            self._send({"version": "9.9.9-test"})
        elif self.path == "/api/tags":
            self._send({"models": FAKE_MODELS})
        elif self.path == "/api/ps":
            self._send({"models": [FAKE_MODELS[1]]})
        else:
            self._send({"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Handler.received.append(payload)
        if self.path == "/api/chat":
            text = "".join(
                message.get("content", "") for message in payload.get("messages", [])
                if message.get("role") == "system"
            )
            facts = [token for token in ("slot 11", "Kuca casti", "VALIDATED", "Pa3X", "SHA-256")
                     if token in text]
            answer = "FAKTOVI: " + ", ".join(facts)
            events = [
                {"message": {"role": "assistant", "content": part}, "done": False}
                for part in [answer[index:index + 12] for index in range(0, len(answer), 12)]
            ]
            events.append({"message": {"role": "assistant", "content": ""}, "done": True,
                           "done_reason": "stop", "eval_count": 7, "prompt_eval_count": 100})
            self._ndjson(events)
        elif self.path == "/api/pull":
            self._ndjson([{"status": "success"}])
        else:
            self._send({"error": "not found"})


class _Server:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def sample_report() -> dict:
    return {
        "facts": {
            "identity": {"filename": "USER09.STY", "size": 452369,
                         "hashes": {"sha256": "a" * 64}},
            "formats": [{"fact": "RAW_BINARY", "status": "OBSERVED", "confidence": 1.0}],
        },
        "evidence": {"entropy": {"whole_file": {"value": 7.3265, "classification": "VERY_HIGH"},
                                 "high_entropy_regions": [{"start": 4096, "size": 4096}]}},
        "decoded": {
            "results": [{
                "decoder": "korg-style-sty",
                "status": "DECODED_VALIDATED",
                "container_kind": "native-container",
                "resource_count": 33,
                "compressed_resource_count": 32,
                "raw_resource_count": 1,
                "resource_decode_errors": 0,
                "validation": {"status": "VALIDATED", "evidence": ["32 compressed resource(s) decoded"]},
                "name_agreement": {"compared": 16, "agreed": 15, "disagreed": 1},
                "slots": [
                    {"slot": 11, "directory_name": "Kuca casti", "decoded_name": "Evo dzepa",
                     "agrees": False, "element_count": 13, "element_track_geometries": {"28/80": 13}},
                    {"slot": 0, "directory_name": "Bulevar revol", "decoded_name": "Bulevar revol",
                     "agrees": True, "element_count": 13, "element_track_geometries": {"28/80": 13}},
                ],
                "resource_errors": [],
            }],
            "probes": [],
        },
        "manuals": {"matches": [{
            "brand": "Korg", "model": "Pa3X",
            "container_era": "pkg/upd installer wrapping OC20 pages",
            "documents": [{"doc": "Owner's Manual", "depth": "reference", "size": "70.4MB"}],
        }]},
    }


class HostTests(unittest.TestCase):
    def test_default_host_is_local(self) -> None:
        self.assertEqual(normalize_host(None) if not __import__("os").environ.get("OLLAMA_HOST")
                         else normalize_host(""), "http://127.0.0.1:11434")

    def test_host_forms_are_normalised(self) -> None:
        self.assertEqual(normalize_host("localhost:11434"), "http://localhost:11434")
        self.assertEqual(normalize_host("http://box:9999/"), "http://box:9999")
        self.assertEqual(normalize_host("127.0.0.1:11434"), "http://127.0.0.1:11434")


class ModelInfoTests(unittest.TestCase):
    def test_human_size_switches_units(self) -> None:
        self.assertEqual(ModelInfo(name="m", size=0).human_size, "-")
        self.assertEqual(ModelInfo(name="m", size=500 * 1024 ** 2).human_size, "500 MB")
        self.assertEqual(ModelInfo(name="m", size=4683087332).human_size, "4.4 GB")

    def test_label_includes_parameters_and_quantisation(self) -> None:
        label = ModelInfo(name="qwen2.5:7b", size=1024 ** 3, parameters="7.6B",
                          quantization="Q4_K_M").label
        self.assertIn("qwen2.5:7b", label)
        self.assertIn("7.6B", label)
        self.assertIn("Q4_K_M", label)


class PreferenceTests(unittest.TestCase):
    def test_recommended_size_wins_over_smaller(self) -> None:
        models = [ModelInfo(name="qwen2.5:3b"), ModelInfo(name="qwen2.5:7b"),
                  ModelInfo(name="qwen2.5:1.5b")]
        self.assertEqual(suggest_model(models), "qwen2.5:7b")

    def test_falls_back_to_the_largest_available(self) -> None:
        self.assertEqual(suggest_model([ModelInfo(name="qwen2.5:1.5b"),
                                        ModelInfo(name="qwen2.5:3b")]), "qwen2.5:3b")

    def test_accepts_a_quantised_tag_for_a_preferred_family(self) -> None:
        self.assertEqual(suggest_model([ModelInfo(name="qwen2.5:7b-instruct-q8_0")]),
                         "qwen2.5:7b-instruct-q8_0")

    def test_uses_any_qwen_when_the_ladder_misses(self) -> None:
        self.assertEqual(suggest_model([ModelInfo(name="qwen3:4b")]), "qwen3:4b")

    def test_uses_any_model_as_a_last_resort(self) -> None:
        self.assertEqual(suggest_model([ModelInfo(name="llama3:8b")]), "llama3:8b")

    def test_empty_installation_returns_empty(self) -> None:
        self.assertEqual(suggest_model([]), "")

    def test_ladder_starts_with_the_recommended_model(self) -> None:
        self.assertEqual(MODEL_PREFERENCE[0], "qwen2.5:7b")


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def client(self) -> OllamaClient:
        return OllamaClient(self.server.host, timeout=10)

    def test_version_and_availability(self) -> None:
        available, detail = self.client().is_available()
        self.assertTrue(available)
        self.assertEqual(detail, "9.9.9-test")

    def test_models_are_listed_and_parsed(self) -> None:
        models = self.client().models()
        self.assertEqual([model.name for model in models], ["qwen2.5:3b", "qwen2.5:7b"])
        self.assertEqual(models[1].parameters, "7.6B")

    def test_resolve_prefers_the_recommended_size(self) -> None:
        self.assertEqual(self.client().resolve_model(), "qwen2.5:7b")

    def test_resolve_honours_an_explicit_request(self) -> None:
        self.assertEqual(self.client().resolve_model("qwen2.5:3b"), "qwen2.5:3b")

    def test_resolve_matches_a_family_when_the_exact_tag_is_absent(self) -> None:
        self.assertEqual(self.client().resolve_model("qwen2.5:14b"), "qwen2.5:7b")

    def test_running_reports_the_loaded_model(self) -> None:
        self.assertEqual(self.client().running(), ["qwen2.5:7b"])

    def test_chat_streams_and_reports_counters(self) -> None:
        pieces: list[str] = []
        result = self.client().chat(
            [{"role": "user", "content": "hello"}], "qwen2.5:7b", on_token=pieces.append
        )
        self.assertTrue(pieces, "the stream produced no fragments")
        self.assertTrue(result["content"])
        self.assertEqual(result["content"], "".join(pieces))
        self.assertEqual(result["eval_count"], 7)
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["done_reason"], "stop")

    def test_chat_can_be_cancelled(self) -> None:
        result = self.client().chat(
            [{"role": "user", "content": "hello"}], "qwen2.5:7b",
            cancel=lambda: True,
        )
        self.assertTrue(result["cancelled"])

    def test_pull_reports_completion(self) -> None:
        events: list[dict] = []
        result = self.client().pull("qwen2.5:7b", on_progress=events.append)
        self.assertEqual(result["status"], "success")
        self.assertTrue(events)

    def test_unreachable_server_raises_a_helpful_error(self) -> None:
        client = OllamaClient("http://127.0.0.1:9", timeout=2)  # discard port
        available, detail = client.is_available()
        self.assertFalse(available)
        self.assertIn("ollama serve", detail.lower())


class PromptTests(unittest.TestCase):
    def test_report_digest_carries_the_measured_facts(self) -> None:
        digest = _compact_report(sample_report(), 20000)
        for expected in ("USER09.STY", "DECODED_VALIDATED", "slot 11", "Kuca casti",
                         "Evo dzepa", "Pa3X", "VALIDATED", "SHA-256"):
            self.assertIn(expected, digest, f"{expected} missing from the digest")

    def test_report_digest_is_capped(self) -> None:
        digest = _compact_report(sample_report(), 400)
        self.assertLessEqual(len(digest), 500)
        self.assertIn("skracen", digest)

    def test_digest_labels_entropy_as_an_indicator_only(self) -> None:
        digest = _compact_report(sample_report(), 20000)
        self.assertIn("NIJE dokaz enkripcije", digest)

    def test_messages_put_rules_before_evidence(self) -> None:
        messages = build_messages("objasni", sample_report())
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
        self.assertEqual(messages[1]["role"], "system")
        self.assertIn("IZVJESTAJ DEKODERA", messages[1]["content"])
        self.assertEqual(messages[-1]["role"], "user")

    def test_messages_include_history_in_order(self) -> None:
        history = [{"role": "user", "content": "prvo"}, {"role": "assistant", "content": "odgovor"}]
        messages = build_messages("drugo", None, history)
        roles = [message["role"] for message in messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "drugo")

    def test_system_prompt_forbids_invention_and_writing(self) -> None:
        lowered = SYSTEM_PROMPT.casefold()
        self.assertIn("ne izmisljaj", lowered)
        self.assertIn("read-only", lowered)
        self.assertIn("to nije u ovom izvjestaju", lowered)

    def test_summary_line_describes_the_loaded_report(self) -> None:
        line = report_summary_line(sample_report())
        self.assertIn("USER09.STY", line)
        self.assertIn("33", line)
        self.assertIn("DECODED_VALIDATED", line)

    def test_summary_line_handles_no_report(self) -> None:
        self.assertIn("Nema ucitanog", report_summary_line(None))

    def test_quick_prompts_are_available(self) -> None:
        self.assertGreaterEqual(len(QUICK_PROMPTS), 4)
        for label, prompt in QUICK_PROMPTS:
            self.assertTrue(label and prompt)


class EndToEndTests(unittest.TestCase):
    """The full path: report -> prompt -> local model -> answer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def test_evidence_reaches_the_model(self) -> None:
        from korg_binary_analyzer.ai import ask

        _Handler.received.clear()
        client = OllamaClient(self.server.host, timeout=10)
        result = ask(
            "objasni razlike u imenima",
            sample_report(),
            client,
            model="qwen2.5:7b",
        )
        # The fake model echoes the facts it saw, so the answer proves delivery.
        self.assertIn("slot 11", result["content"])
        self.assertIn("Kuca casti", result["content"])
        self.assertIn("Pa3X", result["content"])

        sent = _Handler.received[-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1]["content"], "objasni razlike u imenima")
        # The assistant must never be handed the raw resource blob list.
        self.assertNotIn("outputs", json.dumps(sent))

    def test_ask_requires_a_model_when_none_is_installed(self) -> None:
        from korg_binary_analyzer.ai import ask

        client = OllamaClient(self.server.host, timeout=10)
        original = client.models
        client.models = lambda: []  # type: ignore[assignment]
        try:
            with self.assertRaises(OllamaError):
                ask("pitanje", None, client, model=None)
        finally:
            client.models = original  # type: ignore[assignment]

    def test_gui_module_imports_without_a_display(self) -> None:
        # The AI tab lives in gui.py; importing it must not require Tk at import time.
        import importlib

        module = importlib.import_module("korg_binary_analyzer.gui")
        self.assertTrue(hasattr(module, "AnalyzerApp"))


if __name__ == "__main__":
    unittest.main()

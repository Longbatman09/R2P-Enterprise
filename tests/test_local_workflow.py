import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agents.local_mem as lm
import agents.orchestrator as orchestrator
import agents.setup_agent as setup_agent
import mcp_servers.vision_extractor as vision_extractor
import mcp_servers.plot_renderer as plot_renderer


class LocalMemTests(unittest.TestCase):
    def test_detect_assignment_test_from_filename(self) -> None:
        self.assertEqual(lm.detect_assignment_test("WTM29.pdf"), "wtm_29")
        self.assertEqual(
            lm.detect_assignment_test("20-06-2026_Jee-Main_WTM 30_INTERNAL.pdf"),
            "jee_main_wtm_30",
        )
        self.assertEqual(lm.detect_assignment_test("UT-2.pdf"), "unit_test_2")

    def test_detect_assignment_test_from_exam_name(self) -> None:
        self.assertEqual(
            lm.detect_assignment_test("report.pdf", "JEE(Main)/WTM-29"),
            "jeemain_wtm-29",
        )

    def test_format_assignment_display_name(self) -> None:
        self.assertEqual(lm.format_assignment_display_name("wtm_29"), "WTM 29")
        self.assertEqual(
            lm.format_assignment_display_name("jee_main_wtm_30"),
            "JEE Main WTM 30",
        )

    def test_list_assignment_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = lm.LOCAL_MEM_DIR
            lm.LOCAL_MEM_DIR = Path(tmpdir) / "local_mem"
            try:
                lm.ensure_local_mem_dir()
                cache_path = lm.LOCAL_MEM_DIR / "wtm_29.json"
                cache_path.write_text(
                    json.dumps(
                        {
                            "assignment_test": "wtm_29",
                            "updated_at": "2026-06-23T10:00:00+00:00",
                            "files": {
                                "WTM29.pdf": {
                                    "prescan": {
                                        "exam_name": "JEE(Main)/WTM-29",
                                        "student_name": "Test Student",
                                        "student_id": "123",
                                    }
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                history = lm.list_assignment_history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["display_name"], "WTM 29")
                self.assertEqual(history[0]["files"], ["WTM29.pdf"])
            finally:
                lm.LOCAL_MEM_DIR = original_dir

    def test_prescan_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = lm.LOCAL_MEM_DIR
            lm.LOCAL_MEM_DIR = Path(tmpdir) / "local_mem"
            try:
                input_dir = Path(tmpdir) / "input"
                input_dir.mkdir()
                sample = input_dir / "WTM29.pdf"
                sample.write_bytes(b"sample-pdf")

                extraction = {
                    "exam_name": "JEE(Main)/WTM-29",
                    "found_student": True,
                    "student_name": "Test Student",
                    "numerical_fields": {"math": 90},
                }
                assignment = lm.detect_assignment_test(sample.name)
                lm.save_cached_prescan(sample.name, str(sample), assignment, extraction)
                cached = lm.get_cached_prescan(sample.name, str(sample), assignment)
                self.assertEqual(cached, extraction)
            finally:
                lm.LOCAL_MEM_DIR = original_dir


class SetupAgentTests(unittest.TestCase):
    def test_setup_writes_clean_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                setup_agent.setup()
                pyproject = Path("scan-agent/pyproject.toml").read_text(encoding="utf-8")
            finally:
                os.chdir(previous_cwd)

        self.assertIn('"google-adk[gcp]>=2.0.0,<3.0.0"', pyproject)
        self.assertEqual(pyproject.count("dependencies = ["), 1)
        self.assertIn('"python-dotenv>=1.0.1"', pyproject)


class OrchestratorTests(unittest.TestCase):
    def test_build_scan_payload_collects_detected_fields(self) -> None:
        payload = orchestrator.build_scan_payload(
            [
                {
                    "found_student": True,
                    "student_name": "A",
                    "student_id": "1",
                    "student_class": "12",
                    "student_section": "A",
                    "exam_name": "Unit Test 2",
                    "data_mode": "grouped",
                    "numerical_fields": {"math": 90, "physics": 80},
                },
                {
                    "found_student": True,
                    "student_name": "A",
                    "student_id": "1",
                    "student_class": "12",
                    "student_section": "A",
                    "exam_name": "Unit Test 3",
                    "data_mode": "grouped",
                    "numerical_fields": {"math": 88, "physics": 81},
                },
            ]
        )

        self.assertEqual(payload["student"]["name"], "A")
        self.assertEqual(payload["exam_name"], "Unit Test")
        self.assertEqual(payload["data_mode"], "grouped")
        self.assertEqual(payload["common_fields"], ["math", "physics"])

    def test_build_scan_payload_intersects_common_students(self) -> None:
        payload = orchestrator.build_scan_payload(
            [
                {
                    "all_students": [
                        {"student_name": "Arjun Ramesh", "student_id": "101"},
                        {"student_name": "Sita Ram", "student_id": "102"},
                        {"student_name": "Vijay Kumar", "student_id": "103"},
                    ]
                },
                {
                    "all_students": [
                        {"student_name": "Sita Ram", "student_id": "102"},
                        {"student_name": "Vijay Kumar", "student_id": "103"},
                        {"student_name": "Rahul Dev", "student_id": "104"},
                    ]
                }
            ]
        )
        self.assertEqual(len(payload["common_students"]), 2)
        self.assertEqual(payload["common_students"][0]["name"], "Sita Ram")
        self.assertEqual(payload["common_students"][1]["name"], "Vijay Kumar")

    def test_prescan_surfaces_full_failure(self) -> None:
        with mock.patch.object(orchestrator.fw, "list_input_files", return_value={"files": ["a.pdf"]}):
            with mock.patch.object(
                orchestrator.ve,
                "extract_report_data",
                side_effect=RuntimeError("missing key"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    orchestrator.prescan_input_files()

        self.assertIn("Pre-scan failed for every report", str(ctx.exception))
        self.assertIn("missing key", str(ctx.exception))


class VisionExtractorTests(unittest.TestCase):
    def test_missing_api_key_raises_clear_error(self) -> None:
        original_client = vision_extractor.client
        vision_extractor.client = None
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                vision_extractor.get_client()
        vision_extractor.client = original_client
        self.assertIn("Gemini API key not configured", str(ctx.exception))

    def test_sequential_test_numbering_schema(self) -> None:
        # Verify test_name exists on the Pydantic schema
        schema = vision_extractor.VisionExtractionResult.model_json_schema()
        properties = schema.get("properties", {})
        self.assertIn("test_name", properties)
        self.assertEqual(properties["test_name"]["type"], "string")

    def test_plot_renderer_uses_test_name(self) -> None:
        mock_data = {
            "results": [
                {
                    "exam_name": "JEE MAIN WTM INTERNAL ANALYSIS",
                    "test_name": "WTM 29",
                    "numerical_fields": {"maths": 90.0},
                    "class_averages": {"maths": 75.0}
                },
                {
                    "exam_name": "JEE MAIN WTM INTERNAL ANALYSIS",
                    "test_name": "WTM 30",
                    "numerical_fields": {"maths": 85.0},
                    "class_averages": {"maths": 76.0}
                }
            ]
        }
        exams, values, avg_values = plot_renderer.get_data_for_field(mock_data, "maths")
        # Should prioritize test_name (Sequential Test Numbering) over exam_name
        self.assertEqual(exams, ["WTM 29", "WTM 30"])
        self.assertEqual(values, [90.0, 85.0])
        self.assertEqual(avg_values, [75.0, 76.0])


class WhispererIntegrationTests(unittest.TestCase):
    def test_whisperer_extractor_handles_markdown(self) -> None:
        import mcp_servers.vision_extractor as vision_extractor
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Candidate Name: VIPPARTI SAHASRA\nSubject Scores:\nMaths: 90\n")
            temp_path = f.name
            
        try:
            with mock.patch("google.genai.Client") as mock_client:
                mock_instance = mock_client.return_value
                mock_response = mock.MagicMock()
                mock_response.parsed = mock.MagicMock()
                mock_response.parsed.model_dump.return_value = {
                    "exam_name": "JEE MAIN WTM INTERNAL ANALYSIS",
                    "test_name": "WTM 29",
                    "data_mode": "single",
                    "found_student": True,
                    "student_name": "VIPPARTI SAHASRA",
                    "numerical_fields": [{"name": "maths", "value": 90.0}],
                    "class_averages": []
                }
                mock_instance.models.generate_content.return_value = mock_response
                
                with mock.patch("mcp_servers.vision_extractor.get_client", return_value=mock_instance):
                    result = vision_extractor.extract_report_data(temp_path, "VIPPARTI SAHASRA", "257423976")
                    
                    self.assertEqual(result["student_name"], "VIPPARTI SAHASRA")
                    self.assertEqual(result["numerical_fields"]["maths"], 90.0)
                    mock_instance.models.generate_content.assert_called_once()
                    
                    # Verify first element of contents list had the text
                    args, kwargs = mock_instance.models.generate_content.call_args
                    contents = kwargs.get("contents", [])
                    self.assertTrue(any("Candidate Name: VIPPARTI SAHASRA" in str(c) for c in contents))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sp500_ai import cli


class CliTests(unittest.TestCase):
    def test_main_exits_nonzero_when_manifest_contains_failures(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "planned_assessments": 4,
                        "completed_assessments": 3,
                        "failed_assessments": 1,
                        "not_attempted_assessments": 0,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("sp500_ai.cli.build_parser") as parser,
                patch("sp500_ai.cli.load_config", return_value=object()),
                patch("sp500_ai.cli.run_experiment", return_value=output),
            ):
                parser.return_value.parse_args.return_value = type(
                    "Args",
                    (),
                    {
                        "config": None,
                        "scope": "pilot",
                        "backend": "openai",
                        "repetitions": 1,
                        "refresh": False,
                        "symbols": None,
                        "limit": None,
                    },
                )()
                with self.assertRaisesRegex(SystemExit, "2"):
                    cli.main()

    def test_main_succeeds_for_complete_manifest(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "planned_assessments": 4,
                        "completed_assessments": 4,
                        "failed_assessments": 0,
                        "not_attempted_assessments": 0,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("sp500_ai.cli.build_parser") as parser,
                patch("sp500_ai.cli.load_config", return_value=object()),
                patch("sp500_ai.cli.run_experiment", return_value=output),
            ):
                parser.return_value.parse_args.return_value = type(
                    "Args",
                    (),
                    {
                        "config": None,
                        "scope": "pilot",
                        "backend": "openai",
                        "repetitions": 1,
                        "refresh": False,
                        "symbols": None,
                        "limit": None,
                    },
                )()
                cli.main()


if __name__ == "__main__":
    unittest.main()

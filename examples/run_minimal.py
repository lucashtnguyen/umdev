"""Run the synthetic utility project in an edit-and-run Python environment.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained; all example assets and coordinates are fictional.
"""

from pathlib import Path

from umdev import run_project

PROJECT_CONFIG = Path(__file__).parent / "minimal" / "project.json"


if __name__ == "__main__":
    result = run_project(PROJECT_CONFIG)
    print(f"Candidate: {result['candidate']}")
    print(f"Manual QA: {result['review']}")

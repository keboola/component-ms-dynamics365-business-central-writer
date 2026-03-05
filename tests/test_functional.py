"""Functional tests for component using VCR cassettes."""

from pathlib import Path

import pytest
from keboola.datadirtest.vcr import VCRDataDirTester, get_test_cases

FUNCTIONAL_DIR = str(Path(__file__).parent / "functional")
COMPONENT_SCRIPT = str(Path(__file__).parent.parent / "src" / "component.py")

# Tests where the component is expected to exit non-zero.
# Value is a substring that must appear in the raised AssertionError.
EXPECTED_FAILURES = {
    "03_vendor_create_conflict": "1 out of 1 records failed",
}


@pytest.mark.parametrize("test_name", get_test_cases(FUNCTIONAL_DIR))
def test_functional(test_name):
    """Run a single VCR functional test case."""
    tester = VCRDataDirTester(
        data_dir=FUNCTIONAL_DIR,
        component_script=COMPONENT_SCRIPT,
        selected_tests=[test_name],
    )

    if test_name in EXPECTED_FAILURES:
        with pytest.raises(AssertionError) as exc_info:
            tester.run()
        assert EXPECTED_FAILURES[test_name] in str(exc_info.value)
    else:
        tester.run()

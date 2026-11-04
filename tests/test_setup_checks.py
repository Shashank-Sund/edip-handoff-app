"""Setup refuses a catalog name it cannot write into SQL (a hyphen), before any compute, with a clear message."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "setup"))

from load_bronze import check_catalog, load  # noqa: E402


@pytest.mark.parametrize("name", ["edip", "edip_dev", "_x1", "EDIP2"])
def test_plain_catalog_names_pass(name):
    assert check_catalog(name) == name


@pytest.mark.parametrize("name", ["edip-dev", "1edip", "ed ip", "", "edip;drop"])
def test_other_catalog_names_are_refused_with_a_suggestion(name):
    with pytest.raises(SystemExit) as exc:
        check_catalog(name)
    assert "letters, digits and underscores" in str(exc.value)
    if name == "edip-dev":
        assert "'edip_dev'" in str(exc.value)


def test_load_refuses_before_running_any_sql(tmp_path):
    ran = []
    with pytest.raises(SystemExit):
        load(tmp_path, "edip-dev", lambda s, quiet=False: ran.append(s) or [], lambda *a: None)
    assert ran == []

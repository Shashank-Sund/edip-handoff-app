"""Setup refuses a catalog name it cannot write into SQL (a hyphen), before any compute, with a clear message; the
bundle keeps the Lakebase only job in step with setup, and the role map out of the repo."""
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


def _bundle():
    import yaml
    return yaml.safe_load((ROOT / "databricks.yml").read_text())


def test_lakebase_setup_runs_the_same_task_as_setup():
    """edip_lakebase_setup (for a catalog fed by real data) is the lakebase task of edip_setup alone, kept in step."""
    jobs = _bundle()["resources"]["jobs"]
    setup = {t["task_key"]: t for t in jobs["edip_setup"]["tasks"]}
    assert list(setup) == ["data", "pipeline", "handoffs", "lakebase"]
    alone = jobs["edip_lakebase_setup"]
    assert [t["task_key"] for t in alone["tasks"]] == ["lakebase"] and "depends_on" not in alone["tasks"][0]
    assert alone["tasks"][0]["spark_python_task"] == setup["lakebase"]["spark_python_task"]
    deps, setup_deps = (j["environments"][0]["spec"]["dependencies"] for j in (alone, jobs["edip_setup"]))
    assert set(deps) <= set(setup_deps) and any(d.startswith("psycopg") for d in deps)
    assert "schedule" not in alone and "trigger" not in alone


def test_role_map_is_a_deploy_time_variable():
    """Staff emails stay out of the repo: EDIP_ROLE_MAP comes from the role_map variable, empty by default."""
    b = _bundle()
    assert b["variables"]["role_map"]["default"] == "{}"
    env = {e["name"]: e["value"] for e in b["resources"]["apps"]["edip_app"]["config"]["env"]}
    assert env["EDIP_ROLE_MAP"] == "${var.role_map}"
    import re
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", (ROOT / "databricks.yml").read_text())     # no email address
    # The local override file the docs point to (.databricks/bundle/<target>/variable-overrides.json) is ignored.
    assert ".databricks/" in (ROOT / ".gitignore").read_text().split()

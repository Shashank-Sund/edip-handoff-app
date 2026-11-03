import subprocess, sys
from pathlib import Path

SCAN = Path(__file__).resolve().parents[1] / "scripts" / "scan_sensitive.py"

def run(path, env=None):
    return subprocess.run([sys.executable, "-I", str(SCAN), str(path)], capture_output=True, text=True, env=env)

def test_clean_file_passes(tmp_path):
    (tmp_path / "ok.md").write_text("Synthetic data only. Contact: someone@example.com\n")
    assert run(tmp_path, env={"EDIP_BLOCKLIST": str(tmp_path / "none.txt")}).returncode == 0

def test_token_and_host_blocked(tmp_path):
    (tmp_path / "bad.py").write_text('host = "https://' + "dbc-" + "0123abcd-ef01" + '.cloud.databricks.com"\ntok = "dapi' + "0" * 32 + '"\n')
    r = run(tmp_path, env={"EDIP_BLOCKLIST": str(tmp_path / "none.txt")})
    assert r.returncode == 1 and "workspace host" in r.stdout and "databricks token" in r.stdout
    assert "dapi" not in r.stdout  # matched values are never echoed

def test_private_blocklist(tmp_path):
    bl = tmp_path / "bl.txt"; bl.write_text("Acme Health\n")
    (tmp_path / "note.md").write_text("Built for acme health last year\n")
    (tmp_path / "graph.md").write_text("acmehealthy is a different word\n")
    r = run(tmp_path, env={"EDIP_BLOCKLIST": str(bl)})
    assert r.returncode == 1 and "note.md" in r.stdout and "graph.md" not in r.stdout

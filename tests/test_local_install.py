import subprocess
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def test_local_install_scripts_pass_bash_syntax_check():
    scripts = [
        SCRIPTS / "install.sh",
        SCRIPTS / "agent-memory-service.sh",
        SCRIPTS / "uninstall.sh",
    ]

    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_install_dry_run_prints_wrappers_without_creating_targets(tmp_path):
    install_dir = tmp_path / "install"
    bin_dir = tmp_path / "bin"
    vault = tmp_path / "vault"

    result = subprocess.run(
        [
            "bash",
            str(SCRIPTS / "install.sh"),
            "--dry-run",
            "--skip-install",
            "--force",
            "--install-dir",
            str(install_dir),
            "--bin-dir",
            str(bin_dir),
            "--vault",
            str(vault),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "would write" in result.stdout
    assert "memory-mcp" in result.stdout
    assert "AGENT_MEMORY_VAULT" in result.stdout
    assert not install_dir.exists()
    assert not bin_dir.exists()
    assert not vault.exists()


def test_install_help_documents_python_selection():
    result = subprocess.run(
        ["bash", str(SCRIPTS / "install.sh"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "python3.12/3.11/3.10/python3" in result.stdout
    assert "MCP requirement" in result.stdout


def test_cli_module_invocation_runs_typer_app():
    result = subprocess.run(
        [sys.executable, "-m", "agent_memory.cli", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Local-first Obsidian-backed memory CLI" in result.stdout


def test_local_install_docs_reference_existing_scripts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "local-install.md").read_text(encoding="utf-8")
    commands = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")

    for script_name in ("install.sh", "agent-memory-service.sh", "uninstall.sh"):
        assert (SCRIPTS / script_name).exists()
        assert script_name in docs or script_name in commands

    assert "./scripts/install.sh --vault ~/MemoryVault" in readme
    assert "Python 3.10" in readme and "newer" in readme
    assert "agent-memory-service install" in docs
    assert "python3.12" in docs
    assert "systemd --user" in docs
    assert "launchd" in docs

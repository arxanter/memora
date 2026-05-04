import subprocess
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def test_local_install_scripts_pass_bash_syntax_check():
    scripts = [
        SCRIPTS / "install.sh",
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
    assert "memora agent integrate --client all --project /path/to/project --dry-run" in result.stdout
    assert "memora vault set /path/to/initialized-vault" in result.stdout
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
    assert "Python interpreter to use" in result.stdout
    assert "--no-vault" in result.stdout
    assert "wrapper default" in result.stdout


def test_cli_module_invocation_runs_typer_app():
    result = subprocess.run(
        [sys.executable, "-m", "cli", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Local-first Obsidian-backed Memora CLI" in result.stdout


def test_local_install_docs_reference_existing_scripts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    cli_reference = (ROOT / "docs" / "cli-agent-reference.md").read_text(encoding="utf-8")

    for script_name in ("install.sh", "uninstall.sh"):
        assert (SCRIPTS / script_name).exists()

    assert "git clone https://github.com/arxanter/memora.git ~/.local/src/memora" in readme
    assert "./scripts/install.sh" in readme
    assert "Press Enter to use" in readme
    assert "memora init ~/NewMemoryVault --set-default" in readme
    assert "memora vault set ~/ExistingMemoryVault" in readme
    assert "mkdir -p ~/.local/src" in readme
    assert "rm -rf ~/.local/src/memora" in readme
    assert "./scripts/install.sh --force --no-vault" in readme
    assert "./scripts/uninstall.sh --remove-venv" in readme
    assert "normal commands do not need `--vault`" in readme
    assert "Python 3.10" in readme and "newer" in readme
    assert "WSL2" in readme
    assert "CLI command reference for agents" in readme
    assert "raw add" not in readme
    assert "source add" not in readme
    assert "<details>" in readme
    assert "remember" in architecture
    assert "memora raw add <path>" in cli_reference
    assert "memora build-context <task>" in cli_reference

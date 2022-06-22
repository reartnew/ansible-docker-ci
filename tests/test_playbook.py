"""Ansible docker image runner tests"""
# pylint: disable=redefined-outer-name
import os
import typing as t
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml
from ansible.cli.playbook import PlaybookCLI  # type: ignore


class AnsibleTestRunner(t.Protocol):
    """Test runner protocol"""

    def __call__(self, inventory: dict, playbook: list) -> None:
        pass


@pytest.fixture
def temp_ansible_dir() -> t.Generator[Path, None, None]:
    """Prepare ansible context directory"""
    current_dir: str = os.getcwd()
    with TemporaryDirectory() as temp_dir_str:
        temp_path = Path(temp_dir_str)
        # Add plugin
        plugin_path: Path = temp_path / "connection_plugins" / "docker_image.py"
        plugin_path.parent.mkdir()
        plugin_path.write_text("from ansible_docker_ci.image.connection.plugin import *")
        # Change context
        os.chdir(temp_dir_str)
        yield temp_path
        os.chdir(current_dir)


@pytest.fixture
def ansible_runner(temp_ansible_dir: Path) -> AnsibleTestRunner:
    """Prepare test runner callable"""

    def runner(inventory: dict, playbook: list) -> None:
        playbook_file: Path = temp_ansible_dir / "test.yml"
        inventory_file: Path = temp_ansible_dir / "hosts.yml"

        playbook_file.write_text(yaml.safe_dump(playbook))
        inventory_file.write_text(yaml.safe_dump(inventory))
        cli_return_code: int = PlaybookCLI(["__ansible-cli__", str(playbook_file), "-i", str(inventory_file)]).run()
        assert cli_return_code == 0

    return runner


def test_runner(ansible_runner: AnsibleTestRunner):
    """Check base plugin usage"""
    ansible_runner(
        inventory={
            "all": {
                "hosts": {
                    "foobar": {
                        "ansible_connection": "docker_image",
                        "ansible_docker_image": "node:alpine",
                    },
                },
            },
        },
        playbook=[
            {
                "hosts": "all",
                "gather_facts": "no",
                "tasks": [
                    {
                        "raw": "touch /bar",
                    },
                    {
                        "raw": "cat /bar",
                    },
                ],
            },
        ],
    )

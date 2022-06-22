"""Docker image-based connection plugin to make temporary containers"""
import functools
import io
import os
import shutil
import tarfile
import typing as t

from ansible.errors import AnsibleFileNotFound, AnsibleConnectionFailure  # type: ignore
from ansible.plugins.connection import ConnectionBase  # type: ignore
from ansible.plugins.strategy import StrategyBase  # type: ignore
from class_interference import Extension, inject, apply_extensions
from docker.client import DockerClient  # type: ignore
from docker.models.containers import Container  # type: ignore

__all__ = [
    "Connection",
    "DOCUMENTATION",
]

DOCUMENTATION = """
author:
    - Artem Novikov <artnew@list.ru>
name: docker_image
short_description: Run tasks in temporary image-based docker containers
version_added: 1.0.0
description:
    - Allocate temporary docker containers as hosts.
    - Works via docker SDK.
options:
    image:
        type: str
        description:
            - Image identifier.
        vars:
            - name: ansible_docker_image
        keyword:
            - name: image
    host:
        description: Hostname/IP to connect to.
        vars:
            - name: inventory_hostname
            - name: ansible_host
            - name: delegated_vars['ansible_host']
"""


class Connection(ConnectionBase):
    """Local docker based connections"""

    transport = "reartnew.docker.docker_image"
    has_pipelining = True

    DOCKER_CLIENT_CLASS: t.Type[DockerClient] = DockerClient
    _CONTAINER_PID_LABEL: str = "ansible.docker.image.connection.temp_container.parent.pid"
    _CONTAINER_HOSTNAME_LABEL: str = "ansible.docker.image.connection.temp_container.host.name"

    @classmethod
    def list_matching_containers(cls, pid: t.Union[int, str], hostname: t.Optional[str] = None) -> t.List[Container]:
        """List all containers, labeled for this playbook run (defined by PID).
        Optionally filter them by hostname."""
        labels: t.List[str] = [f"{cls._CONTAINER_PID_LABEL}={pid}"]
        if hostname is not None:
            labels.append(f"{cls._CONTAINER_HOSTNAME_LABEL}={hostname}")
        return cls.DOCKER_CLIENT_CLASS.from_env().containers.list(filters={"label": labels})

    # pylint: disable=keyword-arg-before-vararg
    def __init__(self, play_context, new_stdin, shell=None, *args, **kwargs):
        super().__init__(play_context, new_stdin, shell, *args, **kwargs)
        self._client: t.Optional[DockerClient] = None
        self._playbook_pid: str = kwargs["ansible_playbook_pid"]
        self._container: t.Optional[Container] = None

    @functools.cached_property
    def client(self) -> DockerClient:
        """Prepare docker client, if none was created before"""
        return self.DOCKER_CLIENT_CLASS.from_env()

    @functools.cached_property
    def container(self) -> Container:
        """Find matching container, if any, or create it"""
        possible_host_containers: t.List[Container] = self.list_matching_containers(
            pid=self._playbook_pid,
            hostname=self.hostname,
        )
        return (
            possible_host_containers[0]
            if possible_host_containers
            else self.client.containers.run(
                image=self.image,
                command="sh -c 'while :; do sleep 1; done'",
                remove=True,
                detach=True,
                labels={
                    self._CONTAINER_PID_LABEL: self._playbook_pid,
                    self._CONTAINER_HOSTNAME_LABEL: self.hostname,
                },
            )
        )

    def _connect(self) -> None:
        """Create a container and connect to it"""
        super()._connect()
        if not self._connected:
            # Ensure container + health check
            self.container.reload()
            self._connected: bool = True

    @functools.cached_property
    def image(self) -> str:
        """Requested docker image"""
        return self.get_option("image")

    @functools.cached_property
    def hostname(self) -> str:
        """Ansible hostname"""
        return self.get_option("host")

    def exec_command(self, cmd: str, in_data: t.Any = None, sudoable: bool = False) -> t.Tuple[int, bytes, bytes]:
        """Run a command in the container"""
        if in_data is not None:
            raise AnsibleConnectionFailure("`in_data` is not supported yet")
        super().exec_command(cmd, in_data=in_data, sudoable=sudoable)
        exec_data: t.Dict[str, t.Any] = self.client.api.exec_create(
            self.container.id,
            ["sh", "-c", cmd],
            stdout=True,
            stderr=True,
            stdin=False,
        )
        exec_id: str = exec_data["Id"]
        stdout, stderr = self.client.api.exec_start(
            exec_id=exec_id,
            detach=False,
            stream=False,
            socket=False,
            demux=True,
        )
        result: t.Dict[str, t.Any] = self.client.api.exec_inspect(exec_id)
        return result.get("ExitCode") or 0, stdout or b"", stderr or b""

    def put_file(self, in_path: str, out_path: str) -> None:
        """Send a file to the container"""
        super().put_file(in_path, out_path)
        if not out_path.startswith("/"):
            raise AnsibleConnectionFailure("Only absolute paths are available")
        if not os.path.exists(in_path):
            raise AnsibleFileNotFound(in_path)

        exit_code, id_command_stdout, id_command_stderr = self.exec_command("id -u && id -g")
        if exit_code:
            raise AnsibleConnectionFailure(f"Couldn't obtain uid/gid: {id_command_stderr!r}")
        user_id, group_id = map(int, id_command_stdout.splitlines())
        out_dir, out_file = os.path.split(out_path)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", dereference=True, encoding="utf-8") as archive:
            tarinfo = archive.gettarinfo(name=in_path, arcname=out_file)
            tarinfo.uid = user_id
            tarinfo.uname = ""
            tarinfo.gid = group_id
            tarinfo.gname = ""
            tarinfo.mode &= 0o700
            with open(in_path, "rb") as f:
                archive.addfile(tarinfo, fileobj=f)
        put_successful: bool = self.client.api.put_archive(
            container=self.container.id,
            path=out_dir,
            data=stream.getvalue(),
        )
        if not put_successful:
            raise AnsibleConnectionFailure(f"Unknown error while sending file {out_path!r}")

    def fetch_file(self, in_path: str, out_path: str) -> None:
        """Fetch a file from the container"""
        super().fetch_file(in_path, out_path)
        if not out_path.startswith("/"):
            raise AnsibleConnectionFailure("Only absolute paths are available")
        known_in_paths = set()

        while True:
            if in_path in known_in_paths:
                raise AnsibleConnectionFailure(f"Found infinite symbolic link loop: {in_path!r}")
            known_in_paths.add(in_path)
            archive_stream, _ = self.client.api.get_archive(
                container=self.container.id,
                path=in_path,
            )
            stream = io.BytesIO()
            for chunk in archive_stream:
                stream.write(chunk)
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r") as archive:
                members: t.List[tarfile.TarInfo] = list(archive)
                if len(members) != 1:
                    raise AnsibleConnectionFailure(f"Bad members length: {len(members)}")
                member: tarfile.TarInfo = members[0]
                if member.issym():
                    in_path = os.path.join(os.path.split(in_path)[0], member.linkname)
                    continue
                if not member.isfile():
                    raise AnsibleConnectionFailure(f"Bad member: {in_path!r}")
                in_f: t.Optional[t.IO[bytes]] = archive.extractfile(member)
                if in_f is None:
                    raise AnsibleConnectionFailure(f"No member: {member}")
                with open(out_path, "wb") as out_f:
                    shutil.copyfileobj(in_f, out_f, member.size)
                return

    def close(self):
        # Do not terminate containers: they would be stopped on strategy plugin cleanup
        self._connected = False


class StrategyBaseExtension(StrategyBase, Extension):
    """Strategy plugin cleanup extension"""

    @inject
    def cleanup(self):
        self.super_ext.cleanup()
        # Remove all playbook-matching containers
        for container in Connection.list_matching_containers(pid=os.getpid()):  # type: Container
            container.remove(force=True)


# Patch strategy class
apply_extensions(StrategyBaseExtension)

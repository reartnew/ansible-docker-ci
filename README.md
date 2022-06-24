# ansible-docker-ci

Docker image-based dynamic hosts for ansible.

![PyPI - Python Version](https://img.shields.io/pypi/pyversions/ansible-docker-ci)
[![PyPI version](https://badge.fury.io/py/ansible-docker-ci.svg)](https://badge.fury.io/py/ansible-docker-ci)
![Tests](https://github.com/reartnew/ansible-docker-ci/workflows/tests/badge.svg)

## Installation

```shell
# Install python package
pip install ansible-docker-ci
# Add plugin file to your connection plugins directory
echo "from ansible_docker_ci.image.connection.plugin import *" > ./connection_plugins/docker_image.py
```

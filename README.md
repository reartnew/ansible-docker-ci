# ansible-docker-ci

Docker image-based dynamic hosts for ansible.

## Installation

```shell
# Install python package
pip install ansible-docker-ci
# Add plugin file it your connection plugins directory
echo "from ansible_docker_ci.image.connection.plugin import *" > ./connection_plugins/docker_image.py
```

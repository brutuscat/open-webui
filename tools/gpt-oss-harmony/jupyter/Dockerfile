# OpenWebUI documents the Jupyter Docker Stacks minimal-notebook baseline.
# This amd64 digest is intentionally pinned; update it only with a recorded
# image revision, static review, and isolated benchmark.
FROM quay.io/jupyter/minimal-notebook@sha256:644c2e9e91839be7633e1b78d1ffe15e7543cc3355baa48a58b35d36638d73f6

USER root
COPY jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
RUN mkdir -p /home/user && chown 1000:100 /home/user

USER 1000:100
WORKDIR /home/user

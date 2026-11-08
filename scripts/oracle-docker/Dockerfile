# The LibreOffice oracle's environment, as CI's tracked-oracle job had it when
# this file was written: Ubuntu 24.04 with libreoffice-writer and python3-uno
# 4:24.2.7-0ubuntu0.24.04.7. CI installs whatever Ubuntu ships; if CI's result
# and this image's start to differ, compare the two LibreOffice versions first.
#
# Build:  docker build -t aim-oracle:24.2.7 scripts/oracle-docker
# Run:    scripts/run_oracle_docker.sh . OUT_DIR
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer=4:24.2.7-0ubuntu0.24.04.7 \
      python3-uno=4:24.2.7-0ubuntu0.24.04.7 \
 && rm -rf /var/lib/apt/lists/*

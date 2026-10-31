# build-m0.py checks this local tag's immutable ID and the resulting base layers.
# BuildKit's digest resolver requires a registry even for this local-only image.
FROM research/scientist02:functional-check-20261024
USER root
COPY source/ /opt/scientist02/
COPY bridge/ /opt/
COPY source-lock.json runtime_probe.py /opt/m0/
ENV PYTHONPATH=/opt:/opt/scientist02 PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /tmp
RUN python /opt/m0/runtime_probe.py
CMD ["python", "/opt/m0/runtime_probe.py"]

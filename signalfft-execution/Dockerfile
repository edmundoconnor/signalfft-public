FROM python:3.12-slim

RUN groupadd -r signalfft && useradd -r -g signalfft signalfft

WORKDIR /app

# Install common wheel first (dependency)
COPY *.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Copy source and install the execution package
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

USER signalfft

ENTRYPOINT ["python", "-m"]
CMD ["execution.main"]

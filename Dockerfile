FROM python:3.10-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.2.2 torchaudio==2.2.2 --extra-index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /tmp/requirements.txt
# En arm64 `sphn` (dependencia de demucs) no trae wheel: se compila con Rust y se limpia en la misma capa.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config curl ca-certificates \
    && curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal \
    && CMAKE_POLICY_VERSION_MINIMUM=3.5 PATH="/root/.cargo/bin:$PATH" pip install --no-cache-dir -r /tmp/requirements.txt \
    && /root/.cargo/bin/rustup self uninstall -y \
    && apt-get purge -y build-essential cmake pkg-config curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs_6s')"
ENV HF_HOME=/opt/hf TORCH_HOME=/opt/torch DATA_DIR=/data
RUN mkdir -p /opt/hf /data /opt/torch && cp -r /root/.cache/huggingface/. /opt/hf/ \
    && useradd -r -u 10001 -m app && chown -R app:app /data /opt/hf /opt/torch
WORKDIR /srv/server
COPY server/ /srv/server/
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

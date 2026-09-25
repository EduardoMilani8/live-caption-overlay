# live-caption-overlay

Legendas traduzidas em tempo real do áudio de **um** aplicativo (Linux + PipeWire).
Plano em fases: [docs/PLAN.md](docs/PLAN.md).

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,gpu]'   # 'gpu' traz cuBLAS/cuDNN via pip (NVIDIA)
```

Os modelos Whisper são baixados do Hugging Face na primeira execução. Se a API
do Hub limitar seu IP (erro 429, comum em redes compartilhadas), baixe direto:

```sh
D=~/.cache/live-caption/models/small; mkdir -p $D
for f in config.json model.bin tokenizer.json vocabulary.txt; do
  curl -fL -o $D/$f https://huggingface.co/Systran/faster-whisper-small/resolve/main/$f
done
```

## Uso

```sh
.venv/bin/python -m live_caption list                  # apps/abas tocando áudio
.venv/bin/python -m live_caption record --out /tmp/a.wav
.venv/bin/python -m live_caption transcribe            # escolhe a aba, legenda ao vivo
.venv/bin/python -m live_caption transcribe --file fala.wav --lang en
.venv/bin/python -m live_caption bench fala.wav        # tempo por passada, por modelo
.venv/bin/pytest
```

# live-caption-overlay

Legendas traduzidas em tempo real do áudio de **um** aplicativo (Linux + PipeWire).
Plano em fases: [docs/PLAN.md](docs/PLAN.md).

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m live_caption list
.venv/bin/python -m live_caption record firefox --seconds 10 --out /tmp/test.wav
.venv/bin/pytest
```

# GAIA evaluation

Use the general-assistant profile for research questions. It includes public web
search/fetch tools, attachment inspection, a bounded tool budget, and a final
answer format compatible with GAIA-style short answers:

```bash
uv run phycode run --profile gaia "your question"
```

The official GAIA repository is gated on Hugging Face. After accepting its data
terms and downloading it locally, install the optional Parquet and local-audio dependencies and run
the isolated evaluator without copying credentials into the repository:

```powershell
uv sync --extra gaia
uv run python -m phycode.gaia_eval `
  --metadata D:\path\to\GAIA\2023\validation\metadata.parquet `
  --dataset-root D:\path\to\GAIA `
  --credentials D:\path\to\NewTextDocument.txt `
  --limit 10 --audio-model base.en `
  --output .phycode\gaia-results.jsonl --resume
```

Use one or more `--task-id` options for targeted reruns. Audio attachments are
transcribed locally with faster-whisper; the model is downloaded to the local
Hugging Face cache on first use. Pass an empty `--audio-model` value to disable it.

For image attachments, set a vision-capable model in `phycode.toml` or pass
`--vision-model` to the evaluator. If image requests use a different gateway,
also pass its credential block with `--vision-endpoint-index`:

```toml
[llm]
vision_model = "Qwen2.5-VL-72B-Instruct"
```

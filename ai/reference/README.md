# Reference handoffs

`drafts.jsonl.gz` holds every handoff draft that `databricks-gpt-oss-120b` wrote for the reference week (1,320 drafts: 440 generation points, three templates each), exported from `gold.handoff_draft` after a full setup run. The synthetic week is deterministic, so every deploy with the default population produces the same encounters, and setup (`--drafts reference`, the default) loads these instead of calling the model: about 15 minutes instead of 30 to 75.

`jobs/handoff_job.py` loads them only when they match the data exactly: the same generation points (every encounter and time) and the same templates (`templates_sha` in `meta.json`, a hash of `ai/templates/*.yaml`). Otherwise it calls the model for everything and prints why.

To refresh them after changing the generator, the templates or the prompt: deploy with `--var drafts=model`, export the drafts (`ai/eval/export_drafts.py`), then run `python ai/reference/build.py <export.jsonl>`. `tests/test_reference.py` fails while `templates_sha` is out of date, as a reminder.

# assets

- `incoming/` -> manual drops from Christian
- `library/` -> fetched vetted real-photo assets
- `manifest.json` -> source/license/provenance (internal record)

## Fetch random real-photo assets (no-attribution-required)

```bash
cd /home/gruzz/.openclaw/workspace/bloodinthewire/project/scripts
python3 fetch_random_assets.py --count 8
```

The fetcher generates random words in batches of 5, searches Wikimedia Commons,
and only accepts images tagged Public Domain / CC0 style licenses.

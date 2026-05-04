# Stage 0 Planning Notes

This raw inbox example represents unprocessed material from a web clip, pasted
note, or local export. It is safe to preview before importing:

```bash
memora source-inbox scan --vault examples/sample-vault --path examples/sample-vault/raw/inbox --ignore-disabled --dry-run --json
```

When imported, Memora copies normalized source material into `Sources/`.
An agent should then write a concise extract and promote only durable atomic
memories into `Memories/` for review.

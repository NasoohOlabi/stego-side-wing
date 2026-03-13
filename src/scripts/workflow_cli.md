# Workflow CLI Usage

This CLI runs workflow pipelines from the command line via `src/scripts/workflow_cli.py`.

## Run

From repo root:

```bash
uv run python src/scripts/workflow_cli.py <command> [options]
```

Show built-in help:

```bash
uv run python src/scripts/workflow_cli.py -h
uv run python src/scripts/workflow_cli.py <command> -h
```

## Commands

- `data-load` - Load posts in batches.
  - Options: `--count` (default `100`), `--offset` (default `0`), `--batch-size` (default `5`)
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py data-load --count 50 --offset 0 --batch-size 10
    ```

- `research` - Run the research pipeline.
  - Options: `--count` (default `1`), `--offset` (default `0`)
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py research --count 3 --offset 0
    ```

- `gen-angles` - Generate angles for posts.
  - Options: `--count` (default `1`), `--offset` (default `0`)
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py gen-angles --count 2
    ```

- `stego` - Encode a payload for a post.
  - Required: `--post-id`, `--payload`
  - Optional: `--tag`
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py stego --post-id 1ne9f7n --payload "secret message" --tag demo
    ```

- `decode` - Decode index from stego text.
  - Required: `--stego-text`, `--angles-file`
  - Optional: `--few-shots-file`
  - `--angles-file` accepts either:
    - a JSON array of angles, or
    - a JSON object with an `angles` array.
  - `--few-shots-file` must be a JSON array.
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py decode --stego-text "..." --angles-file ./datasets/news_angles/1ne9f7n.json
    ```

- `gen-terms` - Generate search terms from post content.
  - Required: `--post-id`
  - Optional: `--post-title`, `--post-text`, `--post-url`
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py gen-terms --post-id 1ne9f7n --post-title "Title" --post-url "https://example.com"
    ```

- `full` - Run full workflow pipeline.
  - Options: `--start-step` (default `filter-url-unresolved`), `--count` (default `1`)
  - Example:
    ```bash
    uv run python src/scripts/workflow_cli.py full --start-step filter-url-unresolved --count 5
    ```

## Output and Errors

- Success output is printed as formatted JSON to stdout.
- If an error occurs, the CLI prints `Error: <message>` to stderr and exits with code `1`.

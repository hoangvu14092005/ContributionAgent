# Custom Self-Hosted LLM Setup

This guide explains how to configure ContribAI to use your own self-hosted LLM models with per-task routing.

## Overview

ContribAI supports custom self-hosted LLM endpoints that are compatible with the OpenAI API format. You can specify different models for different tasks:

1. **Analysis** - Code analysis and finding detection
2. **Code Generation** - Generating fixes from findings
3. **Review** - Self-review of generated code
4. **Validation** - Validating findings to filter false positives
5. **Issue Solver** - Solving GitHub issues
6. **Context Compression** - Compressing context to fit token limits

## Configuration

### Method 1: Environment Variables (Recommended)

Create a `.env` file in the project root:

```bash
# Copy the example file
cp .env.example .env

# Edit .env with your values
```

Example `.env`:

```bash
# GitHub token
GITHUB_TOKEN=ghp_your_github_token_here

# Custom LLM endpoint
CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
CUSTOM_LLM_API_KEY=your_api_key_here

# Model names per task
LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6
LLM_MODEL_REVIEW=cx/gpt-5.4
LLM_MODEL_VALIDATION=kr/claude-sonnet-4.5
LLM_MODEL_ISSUE_SOLVER=gh/claude-sonnet-4.6
LLM_MODEL_COMPRESSION=gh/claude-sonnet-4.6
```

Then set `provider: "custom"` in `config.yaml`:

```yaml
llm:
  provider: "custom"
  # All other settings will be read from environment variables
```

### Method 2: Direct Configuration in config.yaml

```yaml
llm:
  provider: "custom"
  custom_base_url: "http://localhost:20128/v1"
  api_key: "your_api_key_here"
  custom_models:
    analysis: "ag/gemini-3.1-pro-high"
    code_gen: "gh/claude-sonnet-4.6"
    review: "cx/gpt-5.4"
    validation: "kr/claude-sonnet-4.5"
    issue_solver: "gh/claude-sonnet-4.6"
    compression: "gh/claude-sonnet-4.6"
```

## API Request Format

ContribAI will send requests to your endpoint in OpenAI-compatible format:

```bash
POST http://localhost:20128/v1/chat/completions
Content-Type: application/json
Authorization: Bearer your_api_key_here

{
  "model": "ag/gemini-3.1-pro-high",
  "messages": [
    {
      "role": "system",
      "content": "You are a senior software engineer..."
    },
    {
      "role": "user",
      "content": "Analyze this code for security issues..."
    }
  ],
  "temperature": 0.2,
  "max_tokens": 8192
}
```

Expected response format:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Here is my analysis..."
      }
    }
  ]
}
```

## Task-to-Model Mapping

The system automatically routes requests to the appropriate model based on the current task:

| Task | Default Model | When Used |
|------|--------------|-----------|
| `analysis` | `ag/gemini-3.1-pro-high` | Running code analyzers (security, quality, docs, etc.) |
| `code_gen` | `gh/claude-sonnet-4.6` | Generating code fixes from findings |
| `review` | `cx/gpt-5.4` | Self-reviewing generated code |
| `validation` | `kr/claude-sonnet-4.5` | Validating findings against full file context |
| `issue_solver` | `gh/claude-sonnet-4.6` | Analyzing and solving GitHub issues |
| `compression` | `gh/claude-sonnet-4.6` | Compressing context to fit token limits |
| `default` | `gh/claude-sonnet-4.6` | **Tất cả các thao tác khác** (PR patrol, guidelines parsing, etc.) |

## Logging

When using custom provider, you'll see logs like:

```
INFO: Custom provider initialized: http://localhost:20128/v1 (models: analysis=ag/gemini-3.1-pro-high, code_gen=gh/claude-sonnet-4.6, ...)
INFO: 🤖 Custom LLM call [task=analysis, model=ag/gemini-3.1-pro-high]
INFO: 🤖 Custom LLM call [task=code_gen, model=gh/claude-sonnet-4.6]
```

## Testing Your Setup

1. Start your LLM endpoint:
```bash
# Example: Start your custom LLM server
./start_llm_server.sh
```

2. Test with a single repo:
```bash
# Set environment variables
export CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
export LLM_MODEL_ANALYSIS=ag/gemini-3.1-pro-high
export LLM_MODEL_CODE_GEN=gh/claude-sonnet-4.6

# Run analysis only (no PR creation)
contribai target https://github.com/owner/repo --dry-run
```

3. Check logs for model routing:
```bash
grep "Custom LLM call" ~/.contribai/logs/contribai.log
```

## Troubleshooting

### Connection Errors

If you see connection errors:

```
LLMError: Custom LLM error: Connection refused
```

Check:
- Is your LLM server running?
- Is the base URL correct?
- Can you curl the endpoint?

```bash
curl -X POST http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_api_key" \
  -d '{
    "model": "ag/gemini-3.1-pro-high",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

### Model Not Found

If you see:

```
LLMError: Custom LLM error: Model 'ag/gemini-3.1-pro-high' not found
```

Check:
- Does your endpoint support that model name?
- Are the model names spelled correctly?
- Try listing available models on your endpoint

### Rate Limiting

If you hit rate limits:

```
LLMRateLimitError: Custom LLM rate limit: 429 Too Many Requests
```

The system will automatically retry with exponential backoff. You can also:
- Reduce `max_concurrent_repos` in config.yaml
- Increase `inter_repo_delay_sec` in pipeline config
- Adjust rate limits on your LLM endpoint

## Example: LiteLLM Proxy

If you're using [LiteLLM](https://github.com/BerriAI/litellm) as a proxy:

```bash
# Start LiteLLM proxy
litellm --model gpt-4 --port 20128

# Configure ContribAI
export CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
export LLM_MODEL_ANALYSIS=gpt-4
export LLM_MODEL_CODE_GEN=claude-3-opus
```

## Example: vLLM

If you're using [vLLM](https://github.com/vllm-project/vllm):

```bash
# Start vLLM server
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-2-70b-chat-hf \
  --port 20128

# Configure ContribAI
export CUSTOM_LLM_BASE_URL=http://localhost:20128/v1
export LLM_MODEL_ANALYSIS=meta-llama/Llama-2-70b-chat-hf
```

## Advanced: Per-Task Temperature Override

You can override temperature per task by modifying the code in `contribai/llm/provider.py`:

```python
# In CustomProvider.chat()
task_temperatures = {
    'analysis': 0.2,
    'code_gen': 0.3,
    'review': 0.1,
    'validation': 0.1,
    'issue_solver': 0.2,
    'compression': 0.3,
}
temp = task_temperatures.get(self._current_task, self.temperature)
```

## Support

For issues or questions:
- Check logs: `~/.contribai/logs/contribai.log`
- Enable debug logging: `export LOG_LEVEL=DEBUG`
- Open an issue on GitHub with your configuration (redact sensitive values)

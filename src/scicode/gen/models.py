from functools import partial
from openai import OpenAI
import re
import os

from scicode import keys_cfg_path
from scicode.utils.log import get_logger

logger = get_logger("models")


def get_config():
    if not keys_cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {keys_cfg_path}")
    import config

    return config.Config(str(keys_cfg_path))


def _setting(name: str, *environment_names: str, default: str = "") -> str:
    """Read a setting from ``keys.cfg`` with environment fallbacks."""

    if keys_cfg_path.exists():
        configured = get_config().as_dict().get(name)
        if configured is not None and str(configured).strip():
            return str(configured).strip()
    for environment_name in environment_names:
        value = os.environ.get(environment_name)
        if value and value.strip():
            return value.strip()
    return default


def _openai_base_url(value: str) -> str | None:
    """Normalize either an API base URL or a full chat-completions URL."""

    value = value.rstrip("/")
    suffix = "/chat/completions"
    if value.endswith(suffix):
        value = value[: -len(suffix)]
    return value or None

def generate_litellm_response(prompt: str, *, model: str, **kwargs) -> str:
    """Call the litellm api to generate a response"""
    import litellm
    from litellm.utils import validate_environment as litellm_validate_environment

    # litellm expects all keys as env variables
    config = get_config()
    for key, value in config.as_dict().items():
        if key in os.environ and os.environ[key] != value:
            logger.warning(f"Overwriting {key} from config with environment variable")
        else:
            os.environ[key] = value
    # Let's validate that we have everythong for this model
    env_validation = litellm_validate_environment(model)
    if not env_validation.get("keys_in_environment") or env_validation.get("missing_keys", []):
        msg = f"Environment validation for litellm failed for model {model}: {env_validation}"
        raise ValueError(msg)
    response = litellm.completion(
        model=model,
        messages = [
            {"role": "user", "content": prompt},
        ],
        **kwargs,
    )
    return response.choices[0].message.content

def generate_openai_response(
    prompt: str,
    *,
    model="gpt-4-turbo-2024-04-09",
    temperature: float = 0,
    trace_callback=None,
) -> str:
    """Call an OpenAI Chat Completions-compatible endpoint.

    ``OPENAI_BASE_URL`` may be a base URL (``.../v1``) or the full
    ``.../chat/completions`` URL.  ``trace_callback`` receives the provider
    response object when a caller needs a provider-native audit record.
    """

    key = _setting("OPENAI_KEY", "OPENAI_API_KEY", default="dummy")
    base_url = _openai_base_url(
        _setting("OPENAI_BASE_URL", "OPENAI_API_BASE", default="")
    )
    client_kwargs = {"api_key": key}
    if base_url is not None:
        client_kwargs["base_url"] = base_url
    timeout = _setting("OPENAI_TIMEOUT", "OPENAI_TIMEOUT", default="")
    if timeout:
        client_kwargs["timeout"] = float(timeout)
    max_retries = _setting("OPENAI_MAX_RETRIES", "OPENAI_MAX_RETRIES", default="")
    if max_retries:
        client_kwargs["max_retries"] = int(max_retries)
    client = OpenAI(**client_kwargs)
    completion = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
    )
    if trace_callback is not None:
        trace_callback(completion)
    return completion.choices[0].message.content


def generate_anthropic_response(prompt, *, model="claude-3-opus-20240229",
                                max_tokens: int = 4096, temperature: float = 0) -> str:
    """call the anthropic api to generate a response"""
    import anthropic

    key: str = get_config()["ANTHROPIC_KEY"]  # type: ignore
    client = anthropic.Anthropic(api_key=key)
    message = client.messages.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "user", "content": prompt},
        ],
    )
    return message.content[0].text


def generate_google_response(prompt: str, *, model: str = "gemini-pro",
                             temperature: float = 0) -> str:
    """call the api to generate a response"""
    import google.generativeai as genai

    key: str = get_config()["GOOGLE_KEY"]  # type: ignore
    genai.configure(api_key=key)
    model = genai.GenerativeModel(model_name=model)
    response = model.generate_content(prompt,
                                      generation_config=genai.GenerationConfig(temperature=temperature),
                                      # safety_settings=[
                                      #     {
                                      #         "category": "HARM_CATEGORY_HARASSMENT",
                                      #         "threshold": "BLOCK_NONE",
                                      #     },
                                      #     {
                                      #         "category": "HARM_CATEGORY_HATE_SPEECH",
                                      #         "threshold": "BLOCK_NONE",
                                      #     },
                                      #     {
                                      #         "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                                      #         "threshold": "BLOCK_NONE",
                                      #     },
                                      #     {
                                      #         "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                                      #         "threshold": "BLOCK_NONE"
                                      #     }
                                      # ]
                                      )
    try:
        return response.text
    except ValueError:
        print(f'prompt:\n{prompt}')
        # If the response doesn't contain text, check if the prompt was blocked.
        print(f'prompt feedback:\n{response.prompt_feedback}')
        # Also check the finish reason to see if the response was blocked.
        print(f'finish reason:\n{response.candidates[0].finish_reason.name}')
        # If the finish reason was SAFETY, the safety ratings have more details.
        print(f'safety rating:\n{response.candidates[0].safety_ratings}')
        raise ValueError("Generate response failed.")


def get_model_function(model: str, **kwargs):
    """Return the appropriate function to generate a response based on the model"""
    if model.startswith("litellm/"):
        model = model.removeprefix("litellm/")
        fct = generate_litellm_response
    elif model.startswith("openai/"):
        model = model.removeprefix("openai/")
        fct = generate_openai_response
    elif "gpt" in model:
        fct = generate_openai_response
    elif "claude" in model:
        fct = generate_anthropic_response
    elif "gemini" in model:
        fct = generate_google_response
    elif model == "dummy":
        fct = generate_dummy_response
    else:
        raise ValueError(f"Model {model} not supported")
    return partial(fct, model=model, **kwargs)


def generate_dummy_response(prompt: str, **kwargs) -> str:
    """Used for testing as a substitute for actual models"""
    return "Blah blah\n```python\nprint('Hello, World!')\n```\n"


def extract_python_script(response: str):
    # We will extract the python script from the response
    if '```' in response:
        python_script = response.split("```python")[1].split("```")[0] if '```python' in response else response.split('```')[1].split('```')[0]
    else:
        print("Fail to extract python code from specific format.")
        python_script = response
    python_script = re.sub(r'^\s*(import .*|from .*\s+import\s+.*)', '', python_script, flags=re.MULTILINE)
    return python_script


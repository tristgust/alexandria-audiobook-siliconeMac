import os
import sys
import json
import re
from types import SimpleNamespace

from llm_client import LLMClient
from script_audit import (
    audit_script_chunk,
    format_audit_summary,
)
from default_prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT


def _script_config_bool(value, default=False):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()

        if lowered in {"1", "true", "yes", "on"}:
            return True

        if lowered in {"0", "false", "no", "off"}:
            return False

    if value is None:
        return default

    return bool(value)


def _script_config_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return parsed if parsed > 0 else default


def _script_metric_rate(value):
    if isinstance(value, (int, float)):
        return f"{value:.2f} tok/s"

    return "n/a"


def _print_script_llm_metrics(result):
    metrics = result.metrics

    print(
        "  Structured response: "
        f"backend={result.backend}, "
        f"validation={result.validation_mode}"
    )

    print(
        "  "
        f"prompt={metrics.get('prompt_tokens', 'n/a')} tokens "
        f"@ {_script_metric_rate(metrics.get('prompt_tokens_per_second'))}; "
        f"output={metrics.get('output_tokens', 'n/a')} tokens "
        f"@ {_script_metric_rate(metrics.get('output_tokens_per_second'))}"
    )


class _ScriptOpenAIAdapter:
    """Expose the existing completion interface over Alexandria's LLMClient.

    Native Ollama receives structured output, thinking control, validation,
    and corrective retry. Non-Ollama endpoints retain the original raw OpenAI
    compatibility path so the established cleanup and repair code still works.
    """

    def __init__(
        self,
        runtime_client,
        legacy_client=None,
    ):
        self.runtime_client = runtime_client
        self.legacy_client = legacy_client
        self._warned_banned_tokens = False

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    def _create(
        self,
        *,
        model,
        messages,
        temperature=0.6,
        top_p=0.8,
        presence_penalty=0.0,
        max_tokens=4096,
        extra_body=None,
        **kwargs,
    ):
        if self.legacy_client is not None:
            legacy_kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "presence_penalty": presence_penalty,
                "max_tokens": max_tokens,
            }

            if extra_body:
                legacy_kwargs["extra_body"] = extra_body

            legacy_kwargs.update(kwargs)

            return (
                self.legacy_client
                .chat
                .completions
                .create(**legacy_kwargs)
            )

        options = dict(extra_body or {})

        top_k = options.get("top_k")
        min_p = options.get("min_p")
        banned_tokens = options.get(
            "banned_tokens"
        )

        if (
            banned_tokens
            and not self._warned_banned_tokens
        ):
            print(
                "  WARNING: Native Ollama does not expose "
                "Alexandria's banned_tokens option; "
                "the configured list is ignored."
            )

            self._warned_banned_tokens = True

        result = self.runtime_client.complete_json(
            messages=messages,
            contract="script",
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=presence_penalty,
            extra_body=extra_body,
        )

        _print_script_llm_metrics(result)

        content = json.dumps(
            result.data,
            ensure_ascii=False,
        )

        message = SimpleNamespace(
            content=content,
            reasoning=None,
        )

        choice = SimpleNamespace(
            message=message,
            finish_reason=(
                result.metrics.get("done_reason")
                or "stop"
            ),
        )

        usage = SimpleNamespace(
            prompt_tokens=result.metrics.get(
                "prompt_tokens"
            ),
            completion_tokens=result.metrics.get(
                "output_tokens"
            ),
        )

        return SimpleNamespace(
            choices=[choice],
            usage=usage,
        )


def _build_script_llm_client(config):
    llm_config = config.get("llm", {})

    runtime_client = LLMClient(
        base_url=llm_config.get(
            "base_url",
            "http://localhost:11434/v1",
        ),
        api_key=llm_config.get(
            "api_key",
            "local",
        ),
        model_name=llm_config.get(
            "model_name",
            "richardyoung/qwen3-14b-abliterated:Q8_0",
        ),
        backend=llm_config.get(
            "backend",
            "auto",
        ),
        context_length=_script_config_int(
            llm_config.get(
                "context_length",
                40960,
            ),
            40960,
        ),
        keep_alive=llm_config.get(
            "keep_alive",
            -1,
        ),
        thinking=_script_config_bool(
            llm_config.get(
                "thinking",
                False,
            ),
            False,
        ),
        structured_output=_script_config_bool(
            llm_config.get(
                "structured_output",
                True,
            ),
            True,
        ),
        corrective_retry=_script_config_bool(
            llm_config.get(
                "corrective_retry",
                True,
            ),
            True,
        ),
        timeout=_script_config_int(
            llm_config.get(
                "timeout",
                1800,
            ),
            1800,
        ),
    )

    legacy_client = None

    if runtime_client.backend == "openai-compatible":
        from openai import OpenAI

        legacy_client = OpenAI(
            base_url=runtime_client.base_url,
            api_key=runtime_client.api_key,
        )

    adapter = _ScriptOpenAIAdapter(
        runtime_client,
        legacy_client=legacy_client,
    )

    return runtime_client, adapter


def _record_fidelity_audit(
    chunk_num,
    total_chunks,
    attempt,
    audit_result,
):
    summary = format_audit_summary(
        audit_result
    )

    for line in summary:
        print(f"  {line}")

    log_dir = os.path.join(
        os.path.dirname(__file__),
        "..",
        "logs",
    )

    os.makedirs(
        log_dir,
        exist_ok=True,
    )

    log_path = os.path.join(
        log_dir,
        "llm_responses.log",
    )

    with open(
        log_path,
        "a",
        encoding="utf-8",
    ) as log_file:
        log_file.write(
            "\n"
            + "─" * 80
            + "\n"
        )

        log_file.write(
            f"FIDELITY AUDIT CHUNK "
            f"{chunk_num}/{total_chunks} | "
            f"attempt {attempt + 1}\n"
        )

        for line in summary:
            log_file.write(line + "\n")

        log_file.write(
            json.dumps(
                audit_result.to_dict(),
                indent=2,
                ensure_ascii=False,
            )
        )

        log_file.write(
            "\n"
            + "─" * 80
            + "\n"
        )


def _build_fidelity_retry_suffix(
    audit_result,
):
    lines = [
        "",
        "",
        "CRITICAL SOURCE-FIDELITY CORRECTION REQUIRED",
        "",
        (
            "Your previous JSON was structurally valid, "
            "but failed Alexandria's source-fidelity audit."
        ),
        (
            "Regenerate the ENTIRE source chunk. "
            "Do not return only the corrected entries."
        ),
        "",
        "Failures:",
    ]

    for issue in audit_result.blocking_issues[:12]:
        lines.append(
            f"- {issue.code}: {issue.message}"
        )

        if issue.source_text:
            lines.append(
                "  Required source segment: "
                + json.dumps(
                    issue.source_text,
                    ensure_ascii=False,
                )
            )

        if issue.output_text:
            lines.append(
                "  Incorrect output segment: "
                + json.dumps(
                    issue.output_text,
                    ensure_ascii=False,
                )
            )

    lines.extend(
        [
            "",
            "Mandatory correction rules:",
            (
                "- Preserve every dialogue and narration "
                "segment in its original order."
            ),
            (
                "- Keep attribution narration between "
                "the dialogue portions it separates."
            ),
            (
                "- Never merge dialogue across an "
                "intervening narrator segment."
            ),
            (
                "- Preserve punctuation, attribution verbs, "
                "grammar, actions, and descriptive clauses."
            ),
            (
                "- An attribution subject pronoun may be "
                "replaced with the established speaker name "
                "only when genuinely needed for clarity."
            ),
            (
                "- Do not paraphrase, summarize, reorder, "
                "omit, or invent source text."
            ),
            (
                "- Return only the complete corrected JSON "
                "array."
            ),
        ]
    )

    return "\n".join(lines)


def _audit_candidate(
    chunk,
    entries,
    chunk_num,
    total_chunks,
    attempt,
):
    audit_result = audit_script_chunk(
        chunk,
        entries,
    )

    _record_fidelity_audit(
        chunk_num,
        total_chunks,
        attempt,
        audit_result,
    )

    return audit_result

def clean_json_string(text):
    """Clean and extract valid JSON array from LLM response."""
    # Remove thinking tags (various formats used by different models)
    # GLM, DeepSeek, Qwen, etc. use different thinking tag formats
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text)
    text = re.sub(r'<reflection>[\s\S]*?</reflection>', '', text)
    text = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', text)
    # Handle unclosed thinking tags (model started thinking but didn't close)
    text = re.sub(r'<think>[\s\S]*$', '', text)
    text = re.sub(r'<thinking>[\s\S]*$', '', text)

    # Remove markdown code blocks
    if "```" in text:
        # Find content between ```json and ``` or just ``` and ```
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            text = match.group(1).strip()

    # Find the JSON array - match from first [ to its closing ]
    # Use a bracket counter to find the correct closing bracket
    start = text.find('[')
    if start == -1:
        return None

    bracket_count = 0
    end = -1
    in_string = False
    escape_next = False

    for i, char in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end = i + 1
                break

    if end == -1:
        # No closing bracket found, try to salvage
        last_complete = text.rfind('},')
        if last_complete > start:
            return text[start:last_complete+1] + ']'
        return None

    json_text = text[start:end]

    # Clean control characters inside strings (common LLM issue)
    # Replace literal newlines/tabs inside JSON strings with escaped versions
    def fix_control_chars(match):
        s = match.group(0)
        # Replace unescaped control characters
        s = s.replace('\n', '\\n')
        s = s.replace('\r', '\\r')
        s = s.replace('\t', '\\t')
        return s

    # Fix control characters inside string values
    json_text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_control_chars, json_text)

    return json_text


def repair_json_array(json_text):
    """Attempt to repair common JSON array issues from LLM output."""
    if not json_text:
        return None

    def _filter_entries(lst):
        """Keep only dict entries; LLMs sometimes emit bare strings in the array."""
        filtered = [e for e in lst if isinstance(e, dict)]
        if len(filtered) < len(lst):
            print(f"  Warning: Dropped {len(lst) - len(filtered)} non-object entries from LLM JSON array")
        return filtered if filtered else None

    # Try parsing as-is first
    try:
        result = json.loads(json_text)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 1: Add missing commas between objects (}\s*{" -> },\n{")
    fixed = re.sub(r'\}\s*\{', '},\n{', json_text)
    try:
        result = json.loads(fixed)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 2: Remove trailing commas before ]
    fixed = re.sub(r',\s*\]', ']', fixed)
    try:
        result = json.loads(fixed)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 3: Try to extract individual entries and rebuild
    entries = []
    # Match individual JSON objects
    pattern = r'\{\s*"speaker"\s*:\s*"[^"]*"\s*,\s*"text"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*"instruct"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}'
    matches = re.findall(pattern, json_text, re.DOTALL)

    for match in matches:
        try:
            entry = json.loads(match)
            entries.append(entry)
        except json.JSONDecodeError:
            continue

    if entries:
        return entries

    # Fix 4: Last resort - find last complete entry and truncate
    last_complete = json_text.rfind('},')
    if last_complete > 0:
        try:
            truncated = json_text[:last_complete+1] + ']'
            # Ensure it starts with [
            if not truncated.strip().startswith('['):
                truncated = '[' + truncated
            result = json.loads(truncated)
            if isinstance(result, list):
                return _filter_entries(result)
        except json.JSONDecodeError:
            pass

    return None

def salvage_json_entries(json_text):
    """Last resort: extract individual valid entries with regex."""
    entries = []
    # Match individual JSON objects with speaker, text, instruct fields
    pattern = r'\{\s*"speaker"\s*:\s*"([^"]*)"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"instruct"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    matches = re.finditer(pattern, json_text, re.DOTALL)

    for match in matches:
        try:
            entry = {
                "speaker": match.group(1),
                "text": match.group(2).replace('\\"', '"').replace('\\n', '\n'),
                "instruct": match.group(3).replace('\\"', '"').replace('\\n', '\n')
            }
            entries.append(entry)
        except Exception:
            continue

    return entries if entries else None


def fix_mojibake(text):
    """Fix common mojibake characters resulting from CP1252-as-UTF8."""
    replacements = {
        'â€™': ''',  # Right single quote
        'â€˜': ''',  # Left single quote
        'â€œ': '"',  # Left double quote
        'â€\x9d': '"', # Right double quote
        'â€?': '"', # Sometimes ? if undefined
        'â€"': '—',  # Em dash
        'â€"': '–',  # En dash
        'â€¦': '…',  # Ellipsis
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text

def split_into_chunks(text, max_size=3000):
    """Split text into chunks at paragraph/sentence boundaries."""
    paragraphs = re.split(r'\n\s*\n', text)

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) + 2 > max_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            if len(para) > max_size:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 > max_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence
                    else:
                        current_chunk += " " + sentence if current_chunk else sentence
            else:
                current_chunk = para
        else:
            current_chunk += "\n\n" + para if current_chunk else para

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

def process_chunk(client, model_name, chunk, chunk_num, total_chunks, previous_entries=None, max_retries=2, system_prompt=None, user_prompt_template=None, max_tokens=4096, temperature=0.6, top_p=0.8, top_k=0, min_p=0, presence_penalty=0.0, banned_tokens=None):
    """Process a text chunk and return JSON script entries"""
    # Use provided prompts or fall back to defaults
    sys_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    usr_template = user_prompt_template or DEFAULT_USER_PROMPT

    context_parts = []

    if chunk_num == 1:
        context_parts.append("(Beginning of text)")
    elif chunk_num == total_chunks:
        context_parts.append("(End of text)")
    else:
        context_parts.append(f"(Part {chunk_num} of {total_chunks})")

    if previous_entries and len(previous_entries) > 0:
        # Build character roster for name consistency across chunks
        characters_seen = sorted(set(
            entry.get("speaker", "") for entry in previous_entries
            if entry.get("speaker", "") and entry.get("speaker", "") != "NARRATOR"
        ))
        if characters_seen:
            context_parts.append(f"Characters in this book: {', '.join(characters_seen)}")

        # Include last few entries so the model can maintain style and tone continuity
        tail = previous_entries[-3:]
        context_parts.append("\nPrevious section ended with:")
        for entry in tail:
            context_parts.append(json.dumps(entry, ensure_ascii=False))

    context = "\n".join(context_parts)
    user_prompt = usr_template.format(context=context, chunk=chunk)

    fidelity_retry_suffix = ""

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt + fidelity_retry_suffix}
                ],
                temperature=temperature,
                top_p=top_p,
                presence_penalty=presence_penalty,
                max_tokens=max_tokens,
                extra_body={
                    k: v for k, v in {
                        "top_k": top_k if top_k else None,
                        "min_p": min_p if min_p else None,
                        "banned_tokens": banned_tokens if banned_tokens else None,
                    }.items() if v is not None
                }
            )

            choice = response.choices[0]
            text = choice.message.content.strip()
            finish_reason = choice.finish_reason
            usage = getattr(response, 'usage', None)

            # Log raw response for debugging
            log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "llm_responses.log")
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n{'='*80}\n")
                lf.write(f"CHUNK {chunk_num}/{total_chunks} | attempt {attempt + 1} | finish_reason={finish_reason}\n")
                if usage:
                    lf.write(f"tokens: prompt={getattr(usage, 'prompt_tokens', '?')} completion={getattr(usage, 'completion_tokens', '?')}\n")
                lf.write(f"{'─'*80}\n")
                lf.write(text)
                lf.write(f"\n{'='*80}\n")

            print(f"  finish_reason={finish_reason}", end="")
            if usage:
                print(f" | tokens: prompt={getattr(usage, 'prompt_tokens', '?')} completion={getattr(usage, 'completion_tokens', '?')}", end="")
            print()

            if finish_reason == "length":
                print(f"  WARNING: Response was truncated (hit max_tokens={max_tokens}). Consider increasing max_tokens.")

        except Exception as e:
            print(f"Error calling LLM API (attempt {attempt + 1}): {e}")
            if attempt < max_retries:
                continue
            return []

        # Clean and extract JSON from response
        json_text = clean_json_string(text)

        if not json_text:
            print(f"Warning: Could not find JSON array in chunk {chunk_num} response (attempt {attempt + 1})")
            if attempt < max_retries:
                print("Retrying...")
                continue
            print(f"Response preview: {text[:300]}...")
            return []

        # Try to parse, with repair attempts
        entries = repair_json_array(json_text)

        if entries and len(entries) > 0:
            audit_result = _audit_candidate(
                chunk,
                entries,
                chunk_num,
                total_chunks,
                attempt,
            )

            if audit_result.passed:
                if attempt > 0:
                    print(
                        f"  Succeeded on retry "
                        f"{attempt + 1}"
                    )

                return entries

            if attempt < max_retries:
                fidelity_retry_suffix = (
                    _build_fidelity_retry_suffix(
                        audit_result
                    )
                )

                print(
                    "  Retrying with explicit "
                    "source-fidelity corrections..."
                )

                continue

            print(
                "Error: Final response failed "
                "the source-fidelity audit."
            )

            return []

        # If repair failed, show warning
        print(f"Warning: Could not parse chunk {chunk_num} response as JSON (attempt {attempt + 1})")
        print(f"JSON preview: {json_text[:300]}...")

        if attempt < max_retries:
            print("Retrying with lower temperature...")

        # Last resort: extract individual valid entries with regex
        salvaged_entries = salvage_json_entries(
            json_text
        )

        if salvaged_entries:
            print(
                "Regex-salvaged "
                f"{len(salvaged_entries)} entries "
                "from malformed response"
            )

            audit_result = _audit_candidate(
                chunk,
                salvaged_entries,
                chunk_num,
                total_chunks,
                attempt,
            )

            if audit_result.passed:
                return salvaged_entries

            if attempt < max_retries:
                fidelity_retry_suffix = (
                    _build_fidelity_retry_suffix(
                        audit_result
                    )
                )

                print(
                    "  Salvaged response failed fidelity; "
                    "retrying the complete chunk..."
                )

                continue

            print(
                "Error: Final salvaged response failed "
                "the source-fidelity audit."
            )

            return []

    return []

def main():
    if len(sys.argv) < 2:
        print("Error: No input file path provided.")
        print("Usage: python generate_script.py <input_file_path>")
        sys.exit(1)

    input_file_path = sys.argv[1]
    print(f"Processing book from: {input_file_path}")

    if not os.path.exists(input_file_path):
        print(f"Error: Input file not found: {input_file_path}")
        sys.exit(1)

    with open(input_file_path, 'r', encoding='utf-8') as f:
        book_content = f.read()

    # Fix encoding artifacts
    book_content = fix_mojibake(book_content)

    print(f"Read {len(book_content)} characters")

    # Load LLM config
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config.json: {e}")
    else:
        print("Warning: config.json not found. Using defaults.")

    llm_config = config.get("llm", {})
    base_url = llm_config.get("base_url", "http://localhost:11434/v1")
    api_key = llm_config.get("api_key", "local")
    model_name = llm_config.get("model_name", "richardyoung/qwen3-14b-abliterated:Q8_0")

    # Load custom prompts or use defaults
    prompts_config = config.get("prompts", {})
    system_prompt = prompts_config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    user_prompt_template = prompts_config.get("user_prompt") or DEFAULT_USER_PROMPT

    # Load generation settings
    generation_config = config.get("generation", {})
    chunk_size = generation_config.get("chunk_size", 3000)
    max_tokens = generation_config.get("max_tokens", 4096)
    temperature = generation_config.get("temperature", 0.6)
    top_p = generation_config.get("top_p", 0.8)
    top_k = generation_config.get("top_k", 0)
    min_p = generation_config.get("min_p", 0)
    presence_penalty = generation_config.get("presence_penalty", 0.0)
    banned_tokens = generation_config.get("banned_tokens", [])

    print(f"Connecting to: {base_url}")
    print(f"Using model: {model_name}")
    print(f"Chunk size: {chunk_size} chars, Max tokens: {max_tokens}")
    if banned_tokens:
        print(f"Banned tokens: {banned_tokens}")

    runtime_client, client = _build_script_llm_client(config)
    model_name = runtime_client.model_name

    print(f"LLM backend: {runtime_client.backend}")
    print(
        "LLM thinking: "
        f"{'on' if runtime_client.thinking else 'off'}"
    )
    print(
        "Structured JSON: "
        f"{'on' if runtime_client.structured_output else 'off'}"
    )

    preloaded, preload_message = runtime_client.preload()
    print(preload_message)

    if not preloaded:
        print(
            "Continuing without explicit preload; "
            "the first request may load the model."
        )

    # Split into chunks at natural boundaries
    chunks = split_into_chunks(book_content, max_size=chunk_size)
    total_chunks = len(chunks)

    print(f"Split into {total_chunks} chunks at paragraph/sentence boundaries")

    all_entries = []
    for i, chunk in enumerate(chunks, 1):
        print(f"Processing chunk {i}/{total_chunks} ({len(chunk)} chars)...")

        previous = all_entries if len(all_entries) > 0 else None
        entries = process_chunk(
            client, model_name, chunk, i, total_chunks,
            previous_entries=previous,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=presence_penalty,
            banned_tokens=banned_tokens
        )
        all_entries.extend(entries)
        print(f"  Got {len(entries)} entries")

    if not all_entries:
        print("Error: No script entries generated")
        sys.exit(1)

    # Save as JSON
    output_path = os.path.join("..", "annotated_script.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_entries, f, indent=2, ensure_ascii=False)

    # Delete old chunks.json so editor regenerates from new script
    chunks_path = os.path.join("..", "chunks.json")
    if os.path.exists(chunks_path):
        os.remove(chunks_path)
        print("Cleared old chunks.json")

    # Summary (check both "speaker" and "type" fields)
    speakers = set(entry.get("speaker") or entry.get("type") or "UNKNOWN" for entry in all_entries)
    print(f"\nGenerated {len(all_entries)} script entries")
    print(f"Speakers found: {', '.join(sorted(speakers))}")
    print(f"Output saved to: {output_path}")


if __name__ == '__main__':
    main()

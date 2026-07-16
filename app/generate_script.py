import os
import sys
import json
import time
import re
from types import SimpleNamespace

from llm_client import LLMClient
from script_audit import (
    audit_script_chunk,
    format_audit_summary,
)
from default_prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT
from llm_telemetry import record_llm_pipeline_result


from llm_adapter import (
    ScriptOpenAIAdapter,
    build_script_client,
    metric_rate,
    print_llm_metrics,
)
from llm_config import (
    config_bool,
    config_int,
)

from generation_metadata import (
    GenerationMetadataError,
    build_generation_metadata,
    finalize_generation_outputs,
)

from generation_state import (
    GenerationStateMismatchError,
    load_generation_state,
)

try:
    from generation_state import (
        GenerationStateError,
        atomic_json_write,
        checkpoint_completed_chunk,
        clear_generation_state,
        completed_entries,
        fingerprint_text,
        fingerprint_value,
        prepare_generation_state,
    )
except ImportError:
    from .generation_state import (
        GenerationStateError,
        atomic_json_write,
        checkpoint_completed_chunk,
        clear_generation_state,
        completed_entries,
        fingerprint_text,
        fingerprint_value,
        prepare_generation_state,
    )



# Compatibility names retained for integrations and tests.
# They delegate to shared implementations; no duplicate logic remains.
_ScriptOpenAIAdapter = ScriptOpenAIAdapter
_script_config_bool = config_bool
_script_config_int = config_int
_script_metric_rate = metric_rate


def _print_script_llm_metrics(result):
    return print_llm_metrics(
        "Structured response",
        result,
    )


def _build_script_llm_client(config):
    return build_script_client(config)





def _build_source_segment_contract(
    chunk,
):
    # Build an exact ordered source scaffold for the LLM.
    from script_audit import split_source_segments

    segments = split_source_segments(
        chunk
    )

    lines = [
        "",
        "ORDERED SOURCE-SEGMENT CONTRACT",
        (
            f"The source contains {len(segments)} ordered "
            "dialogue/narration segments."
        ),
        (
            "Represent every numbered item below in this exact order. "
            "Each numbered item is a hard boundary. You may split one "
            "item into multiple adjacent entries only when needed for "
            "TTS length, but never combine different numbered items."
        ),
        (
            "The text value for each entry must reproduce the "
            "listed text exactly. The surrounding spoken-dialogue "
            "quotation marks have already been removed."
        ),
        (
            "For NARRATION items, speaker must be NARRATOR. "
            "For DIALOGUE items, identify the actual speaker from "
            "the adjacent attribution and scene context."
        ),
        (
            "Use one stable, correctly spelled speaker label. "
            "Retain meaningful titles when established, such as "
            "DOCTOR SEN rather than shortening it to SEN."
        ),
        (
            "When a character reads a letter or document aloud, "
            "the prose introducing the reading remains NARRATOR "
            "and the quoted document text belongs to the reader."
        ),
    ]

    for index, segment in enumerate(
        segments,
        start=1,
    ):
        lines.append(
            f"{index}. {segment.kind.upper()} | "
            f"text={json.dumps(segment.text, ensure_ascii=False)}"
        )

    return "\n".join(lines)



def _normalize_speaker_label(
    value,
):
    import re

    return re.sub(
        r"\s+",
        " ",
        str(value).strip().upper(),
    )


def _named_speaker_from_attribution(
    narration_text,
):
    import re

    text = str(narration_text or "").strip()

    speech_verbs = (
        "said|asked|replied|answered|continued|"
        "whispered|shouted|cried|called|murmured|"
        "muttered|declared|added|observed|remarked|"
        "responded|began|finished|demanded|warned|"
        "urged|insisted"
    )

    title = (
        r"(?:Captain|Doctor|Professor|Mister|Miss|"
        r"Ms|Mrs|Mr|Dr|Lady|Lord|Duke|King|Queen|"
        r"Commander|Sergeant|Lieutenant|Admiral|"
        r"General|Inspector|Reverend|Father|Mother|"
        r"Sister|Brother)"
    )
    capitalized = r"[A-Z][A-Za-z'’\-]*"
    titled_name = (
        rf"{title}\s+{capitalized}"
        rf"(?:\s+{capitalized})*"
    )
    ordinary_name = (
        rf"{capitalized}"
        rf"(?:\s+{capitalized})*"
    )
    named = (
        rf"(?:{titled_name}|{ordinary_name})"
    )

    inverted = re.match(
        rf"^(?:{speech_verbs})\s+"
        rf"(?P<speaker>{named})"
        rf"(?=$|[\s,.;:!?])",
        text,
    )

    if inverted is not None:
        return _normalize_speaker_label(
            inverted.group("speaker")
        )

    normal = re.search(
        rf"(?:^|[.!?]\s+)"
        rf"(?:the\s+)?"
        rf"(?P<speaker>{named})\s+"
        rf"(?:{speech_verbs})\b",
        text,
    )

    if normal is not None:
        speaker = normal.group("speaker")

        if speaker.casefold() in {
            "he",
            "she",
            "they",
            "it",
        }:
            return None

        return _normalize_speaker_label(
            speaker
        )

    return None


def _named_reader_from_introduction(
    narration_text,
):
    import re

    text = str(narration_text or "").strip()
    capitalized = r"[A-Z][A-Za-z'’\-]*"

    match = re.match(
        rf"^(?P<speaker>{capitalized}"
        rf"(?:\s+{capitalized})*)\b"
        rf".*\b(?i:read aloud|began reading|"
        rf"continued reading|read)\b",
        text,
    )

    if match is None:
        return None

    speaker = match.group("speaker")

    if speaker.casefold() in {
        "he",
        "she",
        "they",
        "it",
    }:
        return None

    return _normalize_speaker_label(
        speaker
    )


def _canonicalize_dialogue_speakers(
    segments,
    entries,
):
    if len(segments) != len(entries):
        return entries, False

    normalized = [
        dict(entry)
        for entry in entries
    ]
    changed = False

    for index, segment in enumerate(
        segments
    ):
        if segment.kind != "dialogue":
            continue

        resolved = None

        if (
            index + 1 < len(segments)
            and segments[
                index + 1
            ].kind == "narration"
        ):
            resolved = (
                _named_speaker_from_attribution(
                    segments[
                        index + 1
                    ].text
                )
            )

        if (
            resolved is None
            and index > 0
            and segments[
                index - 1
            ].kind == "narration"
        ):
            resolved = (
                _named_reader_from_introduction(
                    segments[
                        index - 1
                    ].text
                )
            )

        if resolved is not None:
            current_label = _normalize_speaker_label(
                normalized[index].get(
                    "speaker",
                    "",
                )
            )
            equivalent_labels = {
                resolved,
                (
                    resolved
                    if resolved.startswith("THE ")
                    else f"THE {resolved}"
                ),
            }

            if current_label not in equivalent_labels:
                normalized[index][
                    "speaker"
                ] = resolved
                changed = True

    return normalized, changed


def _normalize_candidate_to_source_segments(
    chunk,
    entries,
):
    # Restore source-owned text when the candidate has one entry per segment.
    from script_audit import (
        segment_equivalence_mode,
        split_source_segments,
    )

    if not isinstance(entries, list):
        return entries, False

    segments = split_source_segments(
        chunk
    )

    if len(entries) != len(segments):
        return entries, False

    normalized = []

    for segment, entry in zip(
        segments,
        entries,
    ):
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {
                "speaker",
                "text",
                "instruct",
            }
        ):
            return entries, False

        value = dict(entry)
        candidate_text = value.get("text")

        if (
            not isinstance(candidate_text, str)
            or segment_equivalence_mode(
                segment.text,
                candidate_text,
                kind=segment.kind,
            )
            is None
        ):
            value["text"] = segment.text

        if segment.kind == "narration":
            value["speaker"] = "NARRATOR"

        normalized.append(value)

    normalized, speaker_changed = (
        _canonicalize_dialogue_speakers(
            segments,
            normalized,
        )
    )

    return (
        normalized,
        normalized != entries
        or speaker_changed,
    )


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
                "- Represent every item in the ordered source-segment "
                "contract. You may split within one item when needed, "
                "but never combine different numbered items."
            ),
            (
                "- Preserve punctuation, attribution verbs, "
                "grammar, actions, and descriptive clauses."
            ),
            (
                "- Punctuation immediately before a closing "
                "dialogue quote belongs to the dialogue text. "
                "Keep commas such as Stop, and Stay calm, exactly."
            ),
            (
                "- Every NARRATION contract item must use "
                "speaker NARRATOR and must not be combined "
                "with adjacent quoted dialogue."
            ),
            (
                "- For read-aloud material, keep the introducing "
                "prose as NARRATOR and assign the quoted text "
                "to the reader."
            ),
            (
                "- Use stable, correctly spelled speaker labels "
                "and retain meaningful established titles."
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
    chunk_started_at,
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

    record_llm_pipeline_result(
        stage="script",
        unit_kind="chunk",
        unit_index=chunk_num,
        unit_total=total_chunks,
        outer_attempt=attempt + 1,
        unit_elapsed_seconds=(
            time.perf_counter()
            - chunk_started_at
        ),
        audit_kind="script_fidelity",
        audit_result=audit_result.to_dict(),
        expected_contract="script",
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
    chunk_started_at = time.perf_counter()

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
    user_prompt = usr_template.format(
        context=context,
        chunk=chunk,
    )
    user_prompt += _build_source_segment_contract(
        chunk
    )

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
            (
                entries,
                scaffold_applied,
            ) = _normalize_candidate_to_source_segments(
                chunk,
                entries,
            )

            if scaffold_applied:
                print(
                    "  Applied deterministic "
                    "source-segment normalization"
                )

            audit_result = _audit_candidate(
                chunk,
                entries,
                chunk_num,
                total_chunks,
                attempt,
                chunk_started_at,
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

            (
                salvaged_entries,
                scaffold_applied,
            ) = _normalize_candidate_to_source_segments(
                chunk,
                salvaged_entries,
            )

            if scaffold_applied:
                print(
                    "  Applied deterministic "
                    "source-segment normalization"
                )

            audit_result = _audit_candidate(
                chunk,
                salvaged_entries,
                chunk_num,
                total_chunks,
                attempt,
                chunk_started_at,
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


SCRIPT_AUDITOR_CONTRACT_VERSION = 1


def _script_generation_identity(
    *,
    runtime_client,
    base_url,
    model_name,
    system_prompt,
    user_prompt_template,
    chunk_size,
    max_tokens,
    temperature,
    top_p,
    top_k,
    min_p,
    presence_penalty,
    banned_tokens,
):
    return {
        "base_url": base_url,
        "model_name": model_name,
        "backend": runtime_client.backend,
        "thinking": runtime_client.thinking,
        "structured_output": (
            runtime_client.structured_output
        ),
        "corrective_retry": (
            runtime_client.corrective_retry
        ),
        "system_prompt": system_prompt,
        "user_prompt_template": (
            user_prompt_template
        ),
        "chunk_size": chunk_size,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "presence_penalty": (
            presence_penalty
        ),
        "banned_tokens": list(
            banned_tokens or []
        ),
    }

def build_script_generation_snapshot(
    input_file_path,
    *,
    config_path=None,
):
    input_path = os.fspath(input_file_path)

    if not os.path.exists(input_path):
        raise FileNotFoundError(
            f"Input file not found: {input_path}"
        )

    with open(
        input_path,
        "r",
        encoding="utf-8",
    ) as handle:
        book_content = handle.read()

    book_content = fix_mojibake(book_content)

    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__),
            "config.json",
        )

    config = {}

    if os.path.exists(config_path):
        with open(
            config_path,
            "r",
            encoding="utf-8",
        ) as handle:
            config = json.load(handle)

    llm_config = config.get("llm", {})
    base_url = llm_config.get(
        "base_url",
        "http://localhost:11434/v1",
    )
    model_name = llm_config.get(
        "model_name",
        "qwen3.5:35b-mlx",
    )

    prompts_config = config.get("prompts", {})
    system_prompt = (
        prompts_config.get("system_prompt")
        or DEFAULT_SYSTEM_PROMPT
    )
    user_prompt_template = (
        prompts_config.get("user_prompt")
        or DEFAULT_USER_PROMPT
    )

    generation_config = config.get(
        "generation",
        {},
    )
    chunk_size = generation_config.get(
        "chunk_size",
        3000,
    )
    max_tokens = generation_config.get(
        "max_tokens",
        4096,
    )
    temperature = generation_config.get(
        "temperature",
        0.6,
    )
    top_p = generation_config.get(
        "top_p",
        0.8,
    )
    top_k = generation_config.get(
        "top_k",
        0,
    )
    min_p = generation_config.get(
        "min_p",
        0,
    )
    presence_penalty = generation_config.get(
        "presence_penalty",
        0.0,
    )
    banned_tokens = generation_config.get(
        "banned_tokens",
        [],
    )

    runtime_client, _ = (
        _build_script_llm_client(config)
    )
    model_name = runtime_client.model_name

    chunks = split_into_chunks(
        book_content,
        max_size=chunk_size,
    )
    generation_identity = (
        _script_generation_identity(
            runtime_client=runtime_client,
            base_url=base_url,
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt_template=(
                user_prompt_template
            ),
            chunk_size=chunk_size,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=(
                presence_penalty
            ),
            banned_tokens=banned_tokens,
        )
    )

    return {
        "source_path": os.path.abspath(
            input_path
        ),
        "source_basename": os.path.basename(
            input_path
        ),
        "source_character_count": (
            len(book_content)
        ),
        "source_fingerprint": (
            fingerprint_text(book_content)
        ),
        "generation_identity": (
            generation_identity
        ),
        "generation_fingerprint": (
            fingerprint_value(
                generation_identity
            )
        ),
        "chunk_fingerprints": [
            fingerprint_text(chunk)
            for chunk in chunks
        ],
        "total_chunks": len(chunks),
        "auditor_contract_version": (
            SCRIPT_AUDITOR_CONTRACT_VERSION
        ),
    }


def _generate_chunks_with_resume(
    *,
    client,
    model_name,
    chunks,
    state_path,
    source_fingerprint,
    generation_fingerprint,
    process_kwargs,
    resume_info=None,
    generation_identity=None,
    source_info=None,
    auditor_contract_version=None,
):
    chunk_fingerprints = [
        fingerprint_text(chunk)
        for chunk in chunks
    ]
    state = prepare_generation_state(
        path=state_path,
        source_fingerprint=(
            source_fingerprint
        ),
        generation_fingerprint=(
            generation_fingerprint
        ),
        chunk_fingerprints=(
            chunk_fingerprints
        ),
        generation_identity=(
            generation_identity
        ),
        source=source_info,
        auditor_contract_version=(
            auditor_contract_version
        ),
    )

    all_entries = completed_entries(
        state
    )
    completed_count = len(
        state["completed_chunks"]
    )
    total_chunks = len(chunks)

    if resume_info is not None:
        resume_info.clear()
        resume_info.update(
            {
                "resumed": (
                    completed_count > 0
                ),
                "previously_completed_chunks": (
                    completed_count
                ),
            }
        )

    if completed_count:
        print(
            "Resuming script generation after "
            f"{completed_count}/{total_chunks} "
            "completed chunks."
        )

    for index in range(
        completed_count,
        total_chunks,
    ):
        chunk_num = index + 1
        chunk = chunks[index]

        print(
            f"Processing chunk "
            f"{chunk_num}/{total_chunks} "
            f"({len(chunk)} chars)..."
        )

        previous = (
            list(all_entries)
            if all_entries
            else None
        )
        entries = process_chunk(
            client,
            model_name,
            chunk,
            chunk_num,
            total_chunks,
            previous_entries=previous,
            **process_kwargs,
        )

        if not entries:
            raise GenerationStateError(
                "Chunk "
                f"{chunk_num}/{total_chunks} "
                "did not produce an audited result. "
                "Generation state was preserved "
                "for a later resume."
            )

        state = checkpoint_completed_chunk(
            state=state,
            path=state_path,
            index=chunk_num,
            chunk_fingerprint=(
                chunk_fingerprints[index]
            ),
            entries=entries,
        )
        all_entries.extend(
            entries
        )
        print(
            f"  Got {len(entries)} entries"
        )
        print(
            "  Checkpointed chunk "
            f"{chunk_num}/{total_chunks}"
        )

    return all_entries

def finalize_completed_generation_checkpoint(
    input_file_path,
    *,
    root_dir=None,
    config_path=None,
):
    snapshot = build_script_generation_snapshot(
        input_file_path,
        config_path=config_path,
    )

    if root_dir is None:
        root_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
            )
        )
    else:
        root_dir = os.path.abspath(
            os.fspath(root_dir)
        )

    state_path = os.path.join(
        root_dir,
        "generation_state.json",
    )
    state = load_generation_state(
        state_path
    )

    if state is None:
        raise GenerationStateError(
            "No generation checkpoint exists "
            "to finalize."
        )

    mismatches = []

    if (
        state["source_fingerprint"]
        != snapshot["source_fingerprint"]
    ):
        mismatches.append("source")

    if (
        state["generation_fingerprint"]
        != snapshot[
            "generation_fingerprint"
        ]
    ):
        mismatches.append(
            "generation configuration"
        )

    if (
        state["chunk_fingerprints"]
        != snapshot["chunk_fingerprints"]
    ):
        mismatches.append(
            "chunk layout"
        )

    saved_auditor = state.get(
        "auditor_contract_version"
    )

    if (
        saved_auditor is not None
        and saved_auditor
        != snapshot[
            "auditor_contract_version"
        ]
    ):
        mismatches.append(
            "auditor contract"
        )

    if mismatches:
        raise GenerationStateMismatchError(
            "Existing generation state does "
            "not match the current "
            + ", ".join(mismatches)
            + "."
        )

    completed_count = len(
        state["completed_chunks"]
    )
    total_chunks = state[
        "total_chunks"
    ]

    if (
        total_chunks <= 0
        or completed_count
        != total_chunks
    ):
        raise GenerationStateError(
            "Generation checkpoint is not "
            "complete and cannot be finalized "
            "without continuing generation."
        )

    entries = completed_entries(
        state
    )

    if not entries:
        raise GenerationStateError(
            "Completed generation checkpoint "
            "contains no script entries."
        )

    metadata = build_generation_metadata(
        source_path=input_file_path,
        source_fingerprint=(
            snapshot[
                "source_fingerprint"
            ]
        ),
        source_character_count=(
            snapshot[
                "source_character_count"
            ]
        ),
        source_chunk_count=(
            snapshot["total_chunks"]
        ),
        generation_fingerprint=(
            snapshot[
                "generation_fingerprint"
            ]
        ),
        generation_identity=(
            snapshot[
                "generation_identity"
            ]
        ),
        entries=entries,
        resumed=True,
        previously_completed_chunks=(
            completed_count
        ),
    )

    script_path = os.path.join(
        root_dir,
        "annotated_script.json",
    )
    metadata_path = os.path.join(
        root_dir,
        "annotated_script.meta.json",
    )

    finalize_generation_outputs(
        entries=entries,
        metadata=metadata,
        script_path=script_path,
        metadata_path=metadata_path,
        state_path=state_path,
    )

    chunks_path = os.path.join(
        root_dir,
        "chunks.json",
    )

    try:
        os.remove(chunks_path)
    except FileNotFoundError:
        pass

    return {
        "entry_count": len(entries),
        "chunk_count": total_chunks,
        "script_path": script_path,
        "metadata_path": metadata_path,
    }


def main():
    arguments = list(
        sys.argv[1:]
    )
    finalize_only = False

    if (
        arguments
        and arguments[0]
        == "--finalize-only"
    ):
        finalize_only = True
        arguments = arguments[1:]

    if not arguments:
        print(
            "Error: No input file path provided."
        )
        print(
            "Usage: python generate_script.py "
            "[--finalize-only] "
            "<input_file_path>"
        )
        sys.exit(1)

    input_file_path = arguments[0]
    print(
        f"Processing book from: "
        f"{input_file_path}"
    )

    if finalize_only:
        try:
            result = (
                finalize_completed_generation_checkpoint(
                    input_file_path
                )
            )
        except (
            GenerationStateError,
            GenerationMetadataError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            print(
                "Error: Finalization retry "
                f"failed: {exc}"
            )
            print(
                "generation_state.json was "
                "preserved for safe retry."
            )
            sys.exit(1)

        print(
            "Finalized completed generation "
            "checkpoint without regenerating "
            "source chunks."
        )
        print(
            "Generated "
            f"{result['entry_count']} "
            "script entries."
        )
        print(
            "Output saved to: "
            f"{result['script_path']}"
        )
        print(
            "Metadata saved to: "
            f"{result['metadata_path']}"
        )
        return

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
    model_name = llm_config.get("model_name", "qwen3.5:35b-mlx")

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

    root_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
        )
    )
    generation_state_path = os.path.join(
        root_dir,
        "generation_state.json",
    )
    generation_identity = (
        _script_generation_identity(
            runtime_client=runtime_client,
            base_url=base_url,
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt_template=(
                user_prompt_template
            ),
            chunk_size=chunk_size,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=(
                presence_penalty
            ),
            banned_tokens=banned_tokens,
        )
    )
    source_fingerprint = fingerprint_text(
        book_content
    )
    generation_fingerprint = (
        fingerprint_value(
            generation_identity
        )
    )
    resume_info = {}

    try:
        all_entries = (
            _generate_chunks_with_resume(
                client=client,
                model_name=model_name,
                chunks=chunks,
                state_path=(
                    generation_state_path
                ),
                source_fingerprint=(
                    source_fingerprint
                ),
                generation_fingerprint=(
                    generation_fingerprint
                ),
                process_kwargs={
                    "system_prompt": (
                        system_prompt
                    ),
                    "user_prompt_template": (
                        user_prompt_template
                    ),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "min_p": min_p,
                    "presence_penalty": (
                        presence_penalty
                    ),
                    "banned_tokens": (
                        banned_tokens
                    ),
                },
                resume_info=resume_info,
                generation_identity=(
                    generation_identity
                ),
                source_info={
                    "basename": (
                        os.path.basename(
                            input_file_path
                        )
                    ),
                    "character_count": (
                        len(book_content)
                    ),
                },
                auditor_contract_version=(
                    SCRIPT_AUDITOR_CONTRACT_VERSION
                ),
            )
        )
    except GenerationStateError as exc:
        print(
            f"Error: {exc}"
        )
        sys.exit(1)

    if not all_entries:
        print("Error: No script entries generated")
        sys.exit(1)

    output_path = os.path.join(
        root_dir,
        "annotated_script.json",
    )
    metadata_output_path = os.path.join(
        root_dir,
        "annotated_script.meta.json",
    )

    try:
        metadata = build_generation_metadata(
            source_path=input_file_path,
            source_fingerprint=(
                source_fingerprint
            ),
            source_character_count=(
                len(book_content)
            ),
            source_chunk_count=(
                total_chunks
            ),
            generation_fingerprint=(
                generation_fingerprint
            ),
            generation_identity=(
                generation_identity
            ),
            entries=all_entries,
            resumed=resume_info.get(
                "resumed",
                False,
            ),
            previously_completed_chunks=(
                resume_info.get(
                    "previously_completed_chunks",
                    0,
                )
            ),
        )
        finalize_generation_outputs(
            entries=all_entries,
            metadata=metadata,
            script_path=output_path,
            metadata_path=(
                metadata_output_path
            ),
            state_path=(
                generation_state_path
            ),
        )
    except (
        GenerationMetadataError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(
            "Error: Script finalization "
            f"failed: {exc}"
        )
        print(
            "generation_state.json was "
            "preserved for safe retry."
        )
        sys.exit(1)

    print(
        "Cleared completed "
        "generation_state.json"
    )

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
    print(
        "Metadata saved to: "
        f"{metadata_output_path}"
    )


if __name__ == '__main__':
    main()

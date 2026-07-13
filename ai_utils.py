import base64
import io
import json
import re
import traceback

import httpx
from aiogram.types import Message

from bot_config import ai_client, bot, MODEL_CHAT, MODEL_VISION, MODEL_WHISPER, OPENAI_API_KEY, increment_stat


async def describe_photo_bytes(image_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        return ""

    try:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        response = await ai_client.chat.completions.create(
            model=MODEL_VISION,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Кратко опиши изображение на русском в 1–2 фразах, только по содержанию.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                }
            ],
            max_tokens=40,
            temperature=0.2,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        content = getattr(getattr(choices[0], "message", {}), "content", None)
        if content:
            return str(content).strip()
    except Exception as exc:
        if is_openrouter_payment_required_error(exc):
            print("Ошибка распознавания фото: недостаточно средств на OpenRouter для изображения.")
        else:
            print(f"Ошибка распознавания фото: {exc}")
    return ""


async def download_file_bytes(file_id: str) -> bytes:
    try:
        file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        return buffer.getvalue()
    except Exception as exc:
        print(f"Ошибка загрузки файла из Telegram: {exc}")
        return b""


def guess_audio_format(mime_type: str | None) -> str:
    if not mime_type:
        return "ogg"
    mime_type = mime_type.lower()
    if mime_type.endswith("/opus"):
        return "ogg"
    if mime_type.endswith("/x-wav") or mime_type.endswith("/wav") or mime_type.endswith("/pcm"):
        return "wav"
    if mime_type.endswith("/mpeg") or mime_type.endswith("/mp3"):
        return "mp3"
    if mime_type.endswith("/x-flac") or mime_type.endswith("/flac"):
        return "flac"
    if mime_type.endswith("/x-m4a") or mime_type.endswith("/mp4"):
        return "mp4"
    if mime_type.endswith("/webm"):
        return "webm"
    if mime_type.endswith("/ogg"):
        return "ogg"
    if mime_type.endswith("/aac"):
        return "aac"
    return mime_type.split("/")[-1] or "ogg"


def is_openrouter_access_denied_error(error: Exception) -> bool:
    message = str(error).lower()
    if hasattr(error, "response") and error.response is not None:
        try:
            body_text = error.response.text
            if body_text:
                message += " " + body_text.lower()
        except Exception:
            pass
    return "access denied by security policy" in message or "error code: 403" in message or "403" in message


def is_openrouter_payment_required_error(error: Exception) -> bool:
    message = str(error).lower()
    if hasattr(error, "response") and error.response is not None:
        status_code = getattr(error.response, "status_code", None)
        if status_code == 402:
            return True
        try:
            body_text = error.response.text
            if body_text:
                message += " " + body_text.lower()
        except Exception:
            pass
    return "payment required" in message or "requires at least" in message or "balance" in message or "402" in message


async def transcribe_audio_bytes(audio_bytes: bytes, audio_format: str = "ogg") -> str:
    payload = {
        "model": MODEL_WHISPER,
        "input_audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
        "language": "ru",
    }
    url = "https://openrouter.ai/api/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "TelegramQuoteBot/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("text", "").strip()
    except Exception as e:
        if is_openrouter_payment_required_error(e):
            print("Ошибка транскрипции: недостаточно средств на OpenRouter для аудио.")
            return ""
        print(f"Ошибка транскрипции: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                print("Response body:", e.response.text)
            except Exception:
                pass
        traceback.print_exc()
        return ""


async def generate_ai_reply(prompt_text: str, context_text: str | None = None) -> str:
    system_prompt = (
        "Ты — вежливый и полезный ассистент. Отвечай по-русски кратко и по существу, "
        "если просят — можешь дать небольшой совет или шаги. Не добавляй лишних пояснений."
    )
    if context_text:
        system_prompt = (
            f"{system_prompt}\n\n"
            "Ниже — дополнительный контекст о Mortisplay, его сайте и боте. "
            "Если пользователь спрашивает про это, отвечай на его основе; если нет — игнорируй этот контекст.\n"
            f"{context_text}"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]

    try:
        response = await ai_client.chat.completions.create(
            model=MODEL_CHAT,
            messages=messages,
            max_tokens=180,
            temperature=0.7,
            top_p=0.95,
            presence_penalty=0.2,
            frequency_penalty=0.2,
        )
        if not getattr(response, "choices", None):
            return prompt_text.strip()
        choice = response.choices[0]
        content = getattr(getattr(choice, "message", {}), "content", None)
        if content:
            increment_stat("ai_responses")
            return content.strip()
        return prompt_text.strip()
    except Exception as e:
        if is_openrouter_access_denied_error(e):
            print("Ошибка AI reply: доступ к OpenRouter запрещён политикой безопасности.")
            return "Сейчас ИИ недоступен из-за ограничений доступа. Попробуйте позже."
        print(f"Ошибка AI reply: {e}")
        traceback.print_exc()
        return "Произошла ошибка при получении ответа от ИИ."


async def extract_text_source(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    if text:
        return text
    return ""

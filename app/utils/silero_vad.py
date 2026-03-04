import asyncio

from silero_vad import get_speech_timestamps, load_silero_vad, read_audio


async def has_speech_async(audio_path: str) -> bool:
    """Асинхронно проверяет, есть ли речь в аудиофайле (работает в отдельном потоке)."""
    return await asyncio.to_thread(has_speech, audio_path)


def has_speech(audio_path: str) -> bool:
    """Синхронная версия проверки на речь."""
    try:
        model = load_silero_vad()  # Загружаем модель
        wav = read_audio(audio_path)  # Читаем аудиофайл
        speech_timestamps = get_speech_timestamps(wav, model, return_seconds=False)

        return bool(speech_timestamps)  # Если список пуст, значит, речи нет
    except Exception as e:
        print(f"Ошибка при обработке аудио: {e}")
        return False  # В случае ошибки считаем, что речи нет
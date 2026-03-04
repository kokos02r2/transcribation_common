from pydub import AudioSegment
from app.core.logging_config import setup_logging

logger = setup_logging()


def auto_boost_volume(file_path, target_dBFS=-23):
    """ Усиливает громкость, если она слишком низкая. """
    try:
        audio = AudioSegment.from_wav(file_path)
        current_loudness = audio.dBFS

        # Вычисляем, насколько нужно усилить громкость
        boost_dB = target_dBFS - current_loudness

        if boost_dB > 0:
            logger.info(f"🔊 Усиливаем громкость: {current_loudness:.2f} dB → {target_dBFS} dB (+{boost_dB:.2f} dB)")
            audio = audio + boost_dB  # Усиливаем громкость
        else:
            logger.info(f"✅ Громкость нормальная ({current_loudness:.2f} dB), усиление не требуется.")

        # Пересэмплируем в 16 kHz (Whisper требует 16 kHz)
        audio = audio.set_frame_rate(16000).set_channels(1)

        # Сохраняем файл (заменяем оригинал)
        audio.export(file_path, format="wav")

        return file_path  # Возвращаем путь к обработанному файлу

    except Exception as e:
        logger.error(f"❌ Ошибка при усилении громкости: {e}")
        return file_path

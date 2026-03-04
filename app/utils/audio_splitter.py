from pydub import AudioSegment


def split_audio(file_path: str, segment_duration: int = 25) -> list:
    """ Разбивает аудиофайл на куски по segment_duration секунд """
    audio = AudioSegment.from_wav(file_path)
    segments = []
    total_duration = len(audio) // 1000  # в секундах

    for i in range(0, total_duration, segment_duration):
        segment = audio[i * 1000: (i + segment_duration) * 1000]
        segment_path = f"{file_path[:-4]}_part{i // segment_duration}.wav"
        segment.export(segment_path, format="wav")
        segments.append(segment_path)

    return segments

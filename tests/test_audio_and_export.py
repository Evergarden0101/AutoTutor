"""Audio containers, narration and the exporters."""

from __future__ import annotations

import wave

import pytest

from autotutor.config import Settings
from autotutor.content import generate_lesson
from autotutor.export import (
    export_bundle,
    lesson_to_html,
    lesson_to_srt,
    lesson_to_text,
    _srt_timestamp,
)
from autotutor.models import GenerationRequest, Lesson, RubySegment, Sentence
from autotutor.tts import (
    Narrator,
    NarrationResult,
    TTSError,
    SentenceTiming,
    apply_voice_choice,
    available_engines,
    current_voice_choice,
    get_engine,
    list_voice_choices,
)
from autotutor.tts.edge import KNOWN_VOICES, EdgeEngine
from autotutor.tts.audio import (
    Mp3Clip,
    PcmClip,
    UnsupportedFormat,
    concat,
    encode_mp3,
    mp3_available,
    silence_like,
    write_clip,
)


@pytest.fixture
def settings(tmp_path):
    settings = Settings()
    settings.allow_online = False
    settings.output_dir = str(tmp_path / "out")
    return settings


@pytest.fixture
def lesson(settings):
    return generate_lesson(
        GenerationRequest(level="N5", topic="daily_life", length="short", seed=11),
        settings,
    )


class TestPcmClip:
    def test_duration(self):
        clip = PcmClip(b"\x00\x00" * 16000, 16000)
        assert clip.duration == pytest.approx(1.0)

    def test_silence(self):
        clip = PcmClip.silence(500, 8000)
        assert clip.duration == pytest.approx(0.5)
        assert set(clip.data) == {0}

    def test_empty(self):
        assert PcmClip(b"", 16000).is_empty

    def test_wav_round_trip(self, tmp_path):
        clip = PcmClip(b"\x01\x02" * 1000, 22050)
        path = write_clip(clip, tmp_path / "a.wav")
        with wave.open(str(path), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == 22050
            assert handle.getnframes() == 1000

    def test_concat(self):
        a = PcmClip(b"\x00\x00" * 100, 16000)
        b = PcmClip(b"\x00\x00" * 50, 16000)
        assert concat([a, b]).duration == pytest.approx(a.duration + b.duration)

    def test_concat_rejects_mixed_sample_rates(self):
        from autotutor.tts.audio import AudioError

        with pytest.raises(AudioError):
            concat([PcmClip(b"\x00\x00", 16000), PcmClip(b"\x00\x00", 24000)])

    def test_concat_of_nothing(self):
        assert concat([]) is None
        assert concat([PcmClip(b"", 16000)]) is None

    def test_silence_like_matches_container(self):
        assert isinstance(silence_like(PcmClip(b"", 16000), 100), PcmClip)
        assert isinstance(silence_like(Mp3Clip(b"x"), 100), Mp3Clip)


@pytest.mark.skipif(not mp3_available(), reason="lameenc not installed")
class TestMp3:
    def test_encode_produces_a_valid_frame_header(self):
        data = encode_mp3(b"\x00\x00" * 24000, 24000, 128)
        assert len(data) > 500
        assert data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xfa") or data[:3] == b"ID3"

    def test_write_mp3(self, tmp_path):
        clip = PcmClip(b"\x00\x00" * 24000, 24000)
        path = write_clip(clip, tmp_path / "a.mp3", 96)
        assert path.exists() and path.stat().st_size > 500

    def test_mp3_clip_cannot_export_wav(self, tmp_path):
        with pytest.raises(UnsupportedFormat):
            write_clip(Mp3Clip(b"\xff\xfb\x00"), tmp_path / "a.wav")

    def test_unknown_extension(self, tmp_path):
        with pytest.raises(UnsupportedFormat):
            write_clip(PcmClip(b"\x00\x00", 16000), tmp_path / "a.ogg")


class TestEngines:
    def test_registry_is_complete(self):
        for engine_id in ("openjtalk", "sapi5", "edge"):
            engine = get_engine(engine_id)
            assert engine is not None and engine.id == engine_id
            assert engine.name and engine.description

    def test_unknown_engine(self):
        assert get_engine("nope") is None

    def test_unavailable_engines_explain_why(self):
        for engine in (get_engine("openjtalk"), get_engine("sapi5"), get_engine("edge")):
            if not engine.available():
                assert engine.unavailable_reason()

    def test_offline_filter_excludes_network_engines(self):
        assert all(not e.requires_network for e in available_engines(allow_online=False))


class TestEdgeVoices:
    """Guards the bug where the picker offered voices Edge does not serve."""

    def test_shipped_catalogue_is_only_what_edge_actually_serves(self):
        """Azure has more Japanese voices; the free read-aloud endpoint does not.

        Offering ja-JP-AoiNeural and friends made switching voice fail with
        "No audio was received. Please verify that your parameters are
        correct." Do not re-add one without hearing it play.
        """
        assert [v.id for v in KNOWN_VOICES] == [
            "ja-JP-NanamiNeural", "ja-JP-KeitaNeural",
        ]

    def test_every_shipped_voice_is_labelled(self):
        for voice in KNOWN_VOICES:
            assert voice.id.startswith("ja-JP-") and voice.name and voice.gender

    def test_voices_never_blocks_on_the_network(self, monkeypatch):
        engine = get_engine("edge")

        def explode(*args, **kwargs):
            raise AssertionError("voices() must not call the service")

        monkeypatch.setattr(engine, "refresh", explode)
        assert engine.voices()

    def test_refresh_replaces_the_shipped_list(self, monkeypatch):
        engine = EdgeEngine()
        monkeypatch.setattr(engine, "_module", _StubEdgeModule([
            {"ShortName": "ja-JP-NanamiNeural", "Locale": "ja-JP", "Gender": "Female",
             "FriendlyName": "Microsoft Nanami Online (Natural) - Japanese (Japan)"},
            {"ShortName": "ja-JP-NewVoiceNeural", "Locale": "ja-JP", "Gender": "Male",
             "FriendlyName": "Microsoft NewVoice Online (Natural) - Japanese (Japan)"},
            {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female",
             "FriendlyName": "Microsoft Jenny Online (Natural) - English (US)"},
        ]))
        assert engine.refresh() is True
        ids = [v.id for v in engine.voices()]
        assert ids == ["ja-JP-NanamiNeural", "ja-JP-NewVoiceNeural"]
        assert engine.voices()[0].name == "Nanami"
        assert engine.voices()[0].gender == "女性"

    def test_refresh_keeps_the_shipped_list_when_the_service_is_unreachable(self):
        engine = EdgeEngine()
        engine._module = _StubEdgeModule(None)
        assert engine.refresh() is False
        assert [v.id for v in engine.voices()] == [v.id for v in KNOWN_VOICES]

    def test_refresh_ignores_a_response_with_no_japanese(self):
        engine = EdgeEngine()
        engine._module = _StubEdgeModule([
            {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female"},
        ])
        assert engine.refresh() is False
        assert [v.id for v in engine.voices()] == [v.id for v in KNOWN_VOICES]

    def test_a_retired_voice_gets_an_actionable_message(self):
        """An empty stream means the voice is gone, not that the network is down."""
        engine = EdgeEngine()
        engine._module = _StubEdgeModule([], audio=b"")
        with pytest.raises(TTSError) as exc:
            engine.synthesize("こんにちは。", voice="ja-JP-GoneNeural")
        message = str(exc.value)
        assert "ja-JP-GoneNeural" in message
        assert "朗读语音" in message
        assert "网络" not in message

    def test_a_network_failure_still_says_network(self):
        engine = EdgeEngine()
        engine._module = _StubEdgeModule([], error=OSError("connection refused"))
        with pytest.raises(TTSError) as exc:
            engine.synthesize("こんにちは。", voice="ja-JP-NanamiNeural")
        assert "网络" in str(exc.value)


class _StubEdgeModule:
    """Stands in for edge_tts without touching the network."""

    def __init__(self, voices, audio: bytes = b"stub-mp3", error=None):
        self._voices = voices
        self._audio = audio
        self._error = error

    async def list_voices(self):
        if self._voices is None:
            raise OSError("unreachable")
        return self._voices

    def Communicate(self, text, voice, rate="+0%"):  # noqa: N802 - mirrors edge_tts
        return _StubCommunicate(self._audio, self._error)


class _StubCommunicate:
    def __init__(self, audio: bytes, error):
        self._audio = audio
        self._error = error

    async def stream(self):
        if self._error is not None:
            raise self._error
        if self._audio:
            yield {"type": "audio", "data": self._audio}


class TestNarrationFallback:
    """Silence is never an acceptable outcome (see gotcha #8)."""

    def test_a_dead_online_engine_falls_back_to_offline(self, settings, lesson):
        if not get_engine("openjtalk").available():
            pytest.skip("pyopenjtalk not installed")
        settings.allow_online = True
        settings.tts_engine = "edge"
        edge = get_engine("edge")
        if not edge.available():
            pytest.skip("edge-tts not installed")
        original = edge._module
        edge._module = _StubEdgeModule([], audio=b"")
        try:
            result = Narrator(settings).narrate(lesson)
        finally:
            edge._module = original
        assert result.clip is not None
        assert result.engine_id == "openjtalk"
        assert any("已改用" in w for w in result.warnings)

    def test_the_reason_is_reported_once_not_per_sentence(self, settings, lesson):
        edge = get_engine("edge")
        if not edge.available():
            pytest.skip("edge-tts not installed")
        settings.allow_online = True
        settings.tts_engine = "edge"
        original = edge._module
        edge._module = _StubEdgeModule([], audio=b"")
        try:
            result = Narrator(settings).narrate(lesson)
        finally:
            edge._module = original
        retired = [w for w in result.warnings if "停用" in w]
        assert len(retired) == 1, result.warnings

    def test_no_generic_advice_when_a_real_reason_exists(self, settings, lesson):
        edge = get_engine("edge")
        if not edge.available():
            pytest.skip("edge-tts not installed")
        settings.allow_online = False   # no offline fallback path to take
        settings.tts_engine = "edge"
        original = edge._module
        edge._module = _StubEdgeModule([], audio=b"")
        try:
            result = Narrator(settings).narrate(lesson)
        finally:
            edge._module = original
        assert not any("请检查语音引擎设置" in w for w in result.warnings)


class TestSeeking:
    """"Play from this sentence" - byte offsets into the concatenated clip."""

    def _result(self, clips, gap=b"\x00\x00"):
        """A narration of three fake sentences with a gap after each."""
        pieces, timings, offset, elapsed = [], [], 0, 0.0
        for index, data in enumerate(clips):
            clip = PcmClip(data, 16000)
            timings.append(SentenceTiming(index, elapsed, elapsed + clip.duration, offset))
            pieces.append(clip)
            offset += len(data) + len(gap)
            elapsed += clip.duration + PcmClip(gap, 16000).duration
            pieces.append(PcmClip(gap, 16000))
        return NarrationResult(concat(pieces), "openjtalk", "test", timings=timings)

    def test_offset_zero_returns_the_whole_clip(self):
        result = self._result([b"\x01\x02" * 10, b"\x03\x04" * 10])
        assert result.clip_from(0).data == result.clip.data

    def test_later_sentence_starts_at_its_own_bytes(self):
        first, second = b"\x01\x01" * 10, b"\x02\x02" * 10
        result = self._result([first, second])
        assert result.clip_from(1).data.startswith(second)

    def test_seeking_preserves_the_container(self):
        result = self._result([b"\x01\x01" * 10, b"\x02\x02" * 10])
        part = result.clip_from(1)
        assert isinstance(part, PcmClip)
        assert part.sample_rate == result.clip.sample_rate

    def test_an_index_past_the_end_does_not_explode(self):
        result = self._result([b"\x01\x01" * 10])
        assert result.clip_from(99) is not None

    def test_a_negative_index_plays_from_the_start(self):
        result = self._result([b"\x01\x01" * 10, b"\x02\x02" * 10])
        assert result.clip_from(-1).data == result.clip.data

    def test_starts_at_matches_the_timing(self):
        result = self._result([b"\x01\x01" * 100, b"\x02\x02" * 100])
        assert result.starts_at(1) == pytest.approx(result.timings[1].start)
        assert result.starts_at(0) == 0.0

    def test_no_clip_means_nothing_to_seek(self):
        assert NarrationResult(None).clip_from(2) is None
        assert NarrationResult(None).can_seek is False

    def test_mp3_can_seek_even_without_timings_in_seconds(self):
        """concat() joins MP3 byte for byte, so offsets are exact there too."""
        first, second = b"\xff\xfb" + b"a" * 40, b"\xff\xfb" + b"b" * 40
        clip = Mp3Clip(first + second)
        timings = [SentenceTiming(0, 0.0, 0.0, 0), SentenceTiming(1, 0.0, 0.0, len(first))]
        result = NarrationResult(clip, "edge", "Edge", timings=timings)
        assert result.has_timings is False   # seconds are unknown for MP3
        assert result.can_seek is True
        assert result.clip_from(1).data == second

    @pytest.mark.skipif(
        not get_engine("openjtalk").available(), reason="pyopenjtalk not installed"
    )
    def test_offsets_line_up_with_real_audio(self, settings, lesson):
        result = Narrator(settings).narrate(lesson)
        for timing in result.timings:
            part = result.clip_from(timing.index)
            expected = result.duration - timing.start
            assert part.duration == pytest.approx(expected, abs=0.05), timing.index


class TestVoicePicker:
    """The picker flattens engines and voices into one list for the toolbar."""

    def test_offline_voices_come_first(self):
        choices = list_voice_choices(allow_online=True)
        if not choices:
            pytest.skip("no speech engine available in this environment")
        online_seen = False
        for choice in choices:
            if choice.requires_network:
                online_seen = True
            else:
                assert not online_seen, "offline voices must be listed first"

    def test_online_voices_are_hidden_when_networking_is_off(self):
        assert all(not c.requires_network for c in list_voice_choices(allow_online=False))

    def test_keys_are_unique_and_parse_back(self):
        for choice in list_voice_choices(allow_online=True):
            engine_id, _, voice_id = choice.key.partition(":")
            assert engine_id == choice.engine_id and voice_id == choice.voice_id

    def test_apply_writes_both_engine_and_voice(self):
        settings = Settings()
        apply_voice_choice(settings, "edge:ja-JP-KeitaNeural")
        assert settings.tts_engine == "edge"
        assert settings.edge_voice == "ja-JP-KeitaNeural"

    def test_apply_ignores_an_unknown_engine(self):
        settings = Settings()
        before = settings.tts_engine
        apply_voice_choice(settings, "nope:whatever")
        assert settings.tts_engine == before

    def test_round_trip(self):
        settings = Settings()
        apply_voice_choice(settings, "edge:ja-JP-AoiNeural")
        assert current_voice_choice(settings) == "edge:ja-JP-AoiNeural"

    def test_auto_resolves_to_a_concrete_voice(self):
        """The toolbar has to show something, so "auto" is resolved eagerly."""
        settings = Settings()
        settings.tts_engine = "auto"
        assert ":" in current_voice_choice(settings)


@pytest.mark.skipif(
    not get_engine("openjtalk").available(), reason="pyopenjtalk not installed"
)
class TestNarration:
    def test_narrate_produces_audio_and_timings(self, settings, lesson):
        result = Narrator(settings).narrate(lesson)
        assert result.clip is not None
        assert result.engine_id == "openjtalk"
        assert result.duration > 3
        assert result.has_timings
        assert len(result.timings) == len(lesson.sentences)

    def test_timings_are_ordered_and_inside_the_clip(self, settings, lesson):
        result = Narrator(settings).narrate(lesson)
        previous_end = 0.0
        for timing in result.timings:
            assert timing.start >= previous_end - 1e-6
            assert timing.end > timing.start
            previous_end = timing.end
        assert previous_end <= result.duration + 1e-6

    def test_progress_callback_runs(self, settings, lesson):
        seen = []
        Narrator(settings).narrate(lesson, progress=lambda d, t: seen.append((d, t)))
        assert seen and seen[-1][0] == seen[-1][1] == len(lesson.sentences)

    def test_cancellation_stops_early(self, settings, lesson):
        import threading

        cancel = threading.Event()
        cancel.set()
        result = Narrator(settings).narrate(lesson, cancel=cancel)
        assert result.cancelled

    def test_engine_resolution_falls_back_when_offline(self, settings):
        settings.tts_engine = "edge"
        settings.allow_online = False
        engine, warnings = Narrator(settings).resolve_engine()
        assert engine is not None and not engine.requires_network
        assert warnings


class TestTextExports:
    def test_text_export_has_every_section(self, lesson):
        text = lesson_to_text(lesson)
        for marker in ["【日本語（ふりがな付き）】", "【かな だけ / 仅假名】",
                       "【中文翻译】", "【単語 / 生词】"]:
            assert marker in text
        assert lesson.sentences[0].kana in text

    def test_html_export_uses_real_ruby(self, lesson):
        html = lesson_to_html(lesson)
        assert "<ruby>" in html and "<rt>" in html
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert "prefers-color-scheme" in html

    def test_html_escapes_content(self):
        lesson = Lesson(
            title_ja="<script>x</script>",
            sentences=[Sentence(ja="あ", zh="<b>bold</b>", ruby=[RubySegment("あ")])],
        )
        html = lesson_to_html(lesson)
        assert "<script>x</script>" not in html
        assert "&lt;b&gt;bold&lt;/b&gt;" in html

    def test_srt_timestamps(self):
        assert _srt_timestamp(0) == "00:00:00,000"
        assert _srt_timestamp(3661.5) == "01:01:01,500"
        assert _srt_timestamp(-4) == "00:00:00,000"

    def test_srt_body(self, lesson):
        timings = [SentenceTiming(i, i * 2.0, i * 2.0 + 1.5)
                   for i in range(len(lesson.sentences))]
        srt = lesson_to_srt(lesson, timings)
        assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500")
        assert lesson.sentences[0].ja in srt

    def test_srt_ignores_out_of_range_timings(self, lesson):
        srt = lesson_to_srt(lesson, [SentenceTiming(999, 0.0, 1.0)])
        assert srt.strip() == ""


class TestBundle:
    def test_bundle_without_audio(self, lesson, tmp_path):
        result = export_bundle(lesson, tmp_path, include_srt=False, include_json=True)
        names = sorted(p.suffix for p in result.files)
        assert names == [".html", ".json", ".txt"]
        assert all(p.exists() and p.stat().st_size > 0 for p in result.files)

    def test_bundle_warns_when_timings_are_missing(self, lesson, tmp_path):
        result = export_bundle(lesson, tmp_path, include_srt=True, timings=None)
        assert any("字幕" in w for w in result.warnings)

    @pytest.mark.skipif(
        not get_engine("openjtalk").available(), reason="pyopenjtalk not installed"
    )
    def test_full_bundle(self, settings, lesson, tmp_path):
        narration = Narrator(settings).narrate(lesson)
        result = export_bundle(
            lesson, tmp_path, clip=narration.clip, timings=narration.timings,
            stem="lesson", include_json=True,
        )
        suffixes = sorted(p.suffix for p in result.files)
        assert suffixes == [".html", ".json", ".mp3", ".srt", ".txt"]
        assert not result.warnings
        assert (tmp_path / "lesson.mp3").stat().st_size > 5000

    def test_slug_is_filesystem_safe(self, lesson):
        slug = lesson.slug()
        assert not set(slug) & set('\\/:*?"<>| ')
        assert slug.startswith("AutoTutor_")

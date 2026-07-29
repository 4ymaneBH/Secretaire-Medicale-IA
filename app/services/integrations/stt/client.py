"""Speech-to-text client using OpenAI Whisper."""
from typing import Optional, Dict, Any
from openai import AsyncOpenAI, BadRequestError
from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class STTError(Exception):
    pass


class STTService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.whisper_model
        self.supported_languages = settings.supported_languages_list
        # ~0.1s of 8k mono audio ≈ 800 bytes (mu-law). Skip clearly invalid uploads.
        self.min_audio_bytes = 800

    async def transcribe_audio(
        self, audio_data: bytes, language: Optional[str] = None, prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            # Fail fast on obviously invalid audio payloads to avoid OpenAI 400s.
            if not audio_data or len(audio_data) < self.min_audio_bytes:
                logger.warning(
                    f"[STT] Skipping transcription: audio too short ({len(audio_data) if audio_data else 0} bytes)"
                )
                return self._build_short_audio_result(language, len(audio_data) if audio_data else 0)

            # Build enhanced prompt for better context and noise filtering
            enhanced_prompt = self._build_prompt(language, prompt)

            # Use explicit language hint to prevent translation
            whisper_language = language  # "fr", "en", or None for auto-detect

            logger.info(f"[STT] Transcribing: language={whisper_language}, prompt={bool(enhanced_prompt)}")

            response = await self.client.audio.transcriptions.create(
                model=self.model,
                file=("audio.wav", audio_data),
                response_format="verbose_json",
                language=whisper_language,
                prompt=enhanced_prompt,
                temperature=0.0,
            )

            # Extract metrics
            duration = getattr(response, "duration", None)
            confidence = self._extract_confidence(response)
            segments = getattr(response, "segments", [])
            noise_level = self._calculate_noise_level(segments)
            speech_duration = self._calculate_speech_duration(segments)

            result = {
                "text": response.text,
                "language": getattr(response, "language", language or "unknown"),
                "duration": duration,
                "confidence": confidence,
                "segments": segments,
                "noise_level": noise_level,
                "speech_duration": speech_duration,
            }

            # Calculate comprehensive quality score
            quality_result = self.calculate_utterance_quality_score(
                text=response.text,
                confidence=confidence,
                speech_ratio=noise_level.get("speech_ratio"),
                noise_level=noise_level.get("avg_no_speech_prob"),
                audio_duration_sec=duration,
            )

            # Add quality metrics to result
            result["quality_score"] = quality_result["quality_score"]
            result["quality_breakdown"] = quality_result["breakdown"]
            result["is_junk"] = quality_result["is_junk"]
            result["quality_flags"] = quality_result["flags"]

            # Log comprehensive metrics
            self._log_stt_metrics(result)

            return result

        except BadRequestError as exc:
            # Gracefully handle OpenAI's "audio_too_short" errors
            if getattr(exc, "code", "") == "audio_too_short" or "audio file is too short" in str(exc).lower():
                logger.warning(
                    f"[STT] OpenAI rejected audio as too short; returning junk result ({len(audio_data)} bytes)"
                )
                return self._build_short_audio_result(language, len(audio_data))
            logger.error(f"STT transcription failed: {exc}")
            raise STTError(f"Failed to transcribe audio: {exc}")

        except Exception as exc:
            logger.error(f"STT transcription failed: {exc}")
            raise STTError(f"Failed to transcribe audio: {exc}")

    def _build_short_audio_result(self, language: Optional[str], audio_length_bytes: int) -> Dict[str, Any]:
        """Return a minimal junk result for sub-0.1s audio payloads."""
        result = {
            "text": "",
            "language": language or "unknown",
            "duration": 0.0,
            "confidence": None,
            "segments": [],
            "noise_level": {
                "avg_no_speech_prob": None,
                "max_no_speech_prob": None,
                "speech_ratio": None,
            },
            "speech_duration": {
                "total_duration": 0,
                "utterance_count": 0,
                "avg_utterance_duration": 0,
                "characteristics": ["too_short"],
            },
            "quality_score": 0.0,
            "quality_breakdown": {},
            "is_junk": True,
            "quality_flags": ["audio_too_short"],
            "audio_length_bytes": audio_length_bytes,
        }
        # Log as junk for observability but avoid raising to callers.
        self._log_stt_metrics(result)
        return result

    def _build_prompt(self, language: Optional[str] = None, custom_prompt: Optional[str] = None) -> Optional[str]:
        """Build context prompt for Whisper to improve transcription accuracy and filter junk.

        Whisper uses prompts to:
        - Filter out background text (like subtitle sources, URLs, metadata)
        - Provide vocabulary context
        - Improve language-specific recognition
        """
        prompts = {
            "fr": (
                "Ceci est une conversation téléphonique avec la secrétaire médicale virtuelle d'un cabinet médical. "
                "Transcrivez UNIQUEMENT ce que dit le patient/utilisateur, PAS les sous-titres ou métadonnées. "
                "Contexte: prise de rendez-vous, annulation, questions générales sur le cabinet, urgences. "
                "IGNORER ET NE PAS TRANSCRIRE: "
                "'Sous-titres', 'Amara.org', 'Radio-Canada', 'SousTitreur', 'réalisés par', 'YouTube', 'Netflix', "
                "symboles/emojis seuls, URLs, noms de plateformes, crédits, publicités. "
                "Si c'est juste du bruit ou des métadonnées, répondez: [SILENCE] ou [INAUDIBLE]."
            ),
            "en": (
                "This is a phone conversation with a doctor's office's virtual medical secretary. "
                "Transcribe ONLY what the patient/user says, NOT subtitles or metadata. "
                "Context: booking appointments, cancellations, general questions about the practice, emergencies. "
                "IGNORE AND DO NOT TRANSCRIBE: "
                "'Subtitles', 'Amara.org', 'Radio-Canada', 'SousTitreur', 'Subtitles by', 'YouTube', 'Netflix', "
                "emojis or symbols alone, URLs, platform names, credits, ads. "
                "If it's just noise or metadata, respond: auto [SILENCE] or [INAUDIBLE]."
            ),
        }

        # Use custom prompt if provided, otherwise use language-specific prompt
        if custom_prompt:
            return custom_prompt

        # Return appropriate prompt based on language
        if language in prompts:
            return prompts[language]

        # No language specified - don't use a prompt (let Whisper auto-detect)
        return None

    def _extract_confidence(self, response) -> Optional[float]:
        if hasattr(response, "segments") and response.segments:
            confidences = [seg.get("no_speech_prob", 0.0) for seg in response.segments if isinstance(seg, dict)]
            if confidences:
                return 1.0 - (sum(confidences) / len(confidences))
        return None

    def _calculate_noise_level(self, segments: list) -> Dict[str, Any]:
        """Calculate noise level estimate from Whisper segments.

        Whisper provides 'no_speech_prob' for each segment (probability segment is silence/noise).
        Returns a structured noise analysis.
        """
        if not segments:
            return {"avg_no_speech_prob": None, "max_no_speech_prob": None, "speech_ratio": None}

        no_speech_probs = []
        for seg in segments:
            # Handle both dict and Pydantic model segments
            try:
                if isinstance(seg, dict):
                    no_speech_prob = seg.get("no_speech_prob", 0.0)
                else:
                    # Pydantic model - access as attribute
                    no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
                no_speech_probs.append(no_speech_prob)
            except (AttributeError, KeyError):
                pass

        if not no_speech_probs:
            return {"avg_no_speech_prob": None, "max_no_speech_prob": None, "speech_ratio": None}

        avg_no_speech = sum(no_speech_probs) / len(no_speech_probs)
        max_no_speech = max(no_speech_probs)
        speech_ratio = sum(1 for p in no_speech_probs if p < 0.5) / len(no_speech_probs) if no_speech_probs else 0

        return {
            "avg_no_speech_prob": round(avg_no_speech, 3),  # Average probability of no speech
            "max_no_speech_prob": round(max_no_speech, 3),  # Peak noise
            "speech_ratio": round(speech_ratio, 3),         # Ratio of segments with speech (>50%)
        }

    def _calculate_speech_duration(self, segments: list) -> Dict[str, Any]:
        """Calculate speech duration from segments.

        Analyzes segment timings to determine:
        - Total speech duration
        - Number of utterances (breaks in speech)
        - Speech characteristics (too short, too long, etc.)
        """
        if not segments:
            return {
                "total_duration": 0,
                "utterance_count": 0,
                "avg_utterance_duration": 0,
                "characteristics": [],
            }

        # Collect timings from segments
        timings = []
        for seg in segments:
            try:
                if isinstance(seg, dict):
                    start = seg.get("start", 0)
                    end = seg.get("end", 0)
                else:
                    # Pydantic model - access as attributes
                    start = getattr(seg, "start", 0)
                    end = getattr(seg, "end", 0)

                if start is not None and end is not None:
                    timings.append({"start": start, "end": end, "duration": end - start})
            except (AttributeError, KeyError):
                pass

        if not timings:
            return {
                "total_duration": 0,
                "utterance_count": 0,
                "avg_utterance_duration": 0,
                "characteristics": [],
            }

        # Calculate metrics
        total_duration = sum(t["duration"] for t in timings)
        utterance_count = len([t for t in timings if t["duration"] > 0.1])  # Ignore very short segments
        avg_utterance_duration = total_duration / utterance_count if utterance_count > 0 else 0

        # Determine characteristics
        characteristics = []
        if total_duration < 0.5:
            characteristics.append("too_short")  # Less than 0.5 seconds
        elif total_duration > 30:
            characteristics.append("too_long")   # More than 30 seconds
        elif total_duration > 15:
            characteristics.append("long_utterance")

        # Gap analysis (silence between utterances)
        gaps = []
        for i in range(len(timings) - 1):
            gap = timings[i + 1]["start"] - timings[i]["end"]
            if gap > 0.5:  # Gap > 0.5 seconds
                gaps.append(gap)

        if gaps:
            characteristics.append("fragmented")  # Speech with gaps (overlapping with silence or TTS)

        return {
            "total_duration": round(total_duration, 2),
            "utterance_count": utterance_count,
            "avg_utterance_duration": round(avg_utterance_duration, 2),
            "characteristics": characteristics,
        }

    def _log_stt_metrics(self, result: Dict[str, Any]) -> None:
        """Log comprehensive STT metrics for diagnostics and analysis.

        Provides structured logging for:
        - Audio duration and characteristics
        - Confidence scores and noise levels
        - Speech classification
        - Quality indicators
        """
        text = result.get("text", "")
        duration = result.get("duration")
        confidence = result.get("confidence")
        noise_level = result.get("noise_level", {})
        speech_duration = result.get("speech_duration", {})
        quality_score = result.get("quality_score", 0.0)
        quality_breakdown = result.get("quality_breakdown", {})
        is_junk = result.get("is_junk", False)
        quality_flags = result.get("quality_flags", [])

        # Determine audio classification
        classification = self._classify_audio(text, confidence, noise_level, speech_duration)

        # Build structured log with quality metrics
        metrics_log = {
            "event": "stt_transcription_complete",
            "audio_metrics": {
                "total_duration_sec": duration,
                "speech_duration_sec": speech_duration.get("total_duration"),
                "utterance_count": speech_duration.get("utterance_count"),
                "avg_utterance_duration_sec": speech_duration.get("avg_utterance_duration"),
            },
            "speech_quality": {
                "stt_confidence": round(confidence, 3) if confidence else None,
                "noise_level": {
                    "avg_no_speech_prob": noise_level.get("avg_no_speech_prob"),
                    "max_no_speech_prob": noise_level.get("max_no_speech_prob"),
                    "speech_ratio": noise_level.get("speech_ratio"),
                },
            },
            "utterance_quality": {
                "quality_score": round(quality_score, 3),
                "is_junk": is_junk,
                "breakdown": {
                    "length": round(quality_breakdown.get("length", {}).get("score", 0.0), 3),
                    "interrogative": round(quality_breakdown.get("interrogative", {}).get("score", 0.0), 3),
                    "confidence": round(quality_breakdown.get("confidence", {}).get("score", 0.0), 3),
                    "audio_metrics": round(quality_breakdown.get("audio_metrics", {}).get("score", 0.0), 3),
                    "semantic_richness": round(quality_breakdown.get("semantic_richness", {}).get("score", 0.0), 3),
                },
                "flags": quality_flags,
            },
            "speech_characteristics": {
                "flags": speech_duration.get("characteristics", []),
            },
            "classification": classification,
            "text_preview": text[:100] if text else "[EMPTY]",
            "text_length": len(text),
        }

        # Format confidence safely (handle None)
        confidence_str = f"{confidence:.2%}" if confidence is not None else "N/A"
        duration_str = f"{duration:.1f}" if duration is not None else "N/A"
        noise_str = f"{noise_level.get('avg_no_speech_prob'):.3f}" if noise_level.get('avg_no_speech_prob') is not None else "N/A"
        quality_str = f"{quality_score:.2f}"
        junk_str = "⚠️ JUNK" if is_junk else "✓ VALID"

        # Log as structured info with quality score breakdown
        logger.info(
            f"[STT] Quality={quality_str} {junk_str} | "
            f"Audio: {duration_str}s | "
            f"Speech: {speech_duration.get('total_duration', 0):.2f}s ({speech_duration.get('utterance_count', 0)} utterances) | "
            f"Confidence: {confidence_str} | "
            f"Noise: {noise_str} | "
            f"Classification: {classification} | "
            f"Text: '{text[:80]}'",
            extra={
                "metrics": metrics_log,
                "stt_classification": classification,
                "stt_confidence": confidence,
                "audio_duration": duration,
                "noise_level_avg": noise_level.get("avg_no_speech_prob"),
                "quality_score": quality_score,
                "is_junk": is_junk,
                "quality_flags": quality_flags,
                "speech_characteristics": speech_duration.get("characteristics", []),
            }
        )

    def _classify_audio(
        self, text: str, confidence: Optional[float], noise_level: Dict[str, Any],
        speech_duration: Dict[str, Any]
    ) -> str:
        """Classify audio into categories for analysis.

        Returns one of:
        - 'real_speech': User spoke clearly
        - 'junk': Subtitle metadata or noise
        - 'silence': No speech detected
        - 'noise': Background noise without speech
        - 'low_quality': Speech with high noise/low confidence
        """
        # Empty text = silence or noise
        if not text or not text.strip():
            avg_no_speech_prob = noise_level.get("avg_no_speech_prob", 0)
            if avg_no_speech_prob is not None and avg_no_speech_prob > 0.7:
                return "silence"
            return "noise"

        # Check if junk (metadata, subtitles)
        junk_score = self._calculate_junk_score(text.lower(), confidence=confidence)
        if junk_score >= 0.7:
            return "junk"

        # Analyze quality
        speech_ratio = noise_level.get("speech_ratio", 0)
        avg_no_speech = noise_level.get("avg_no_speech_prob", 0)
        total_duration = speech_duration.get("total_duration", 0)

        # Low quality: low confidence + high noise
        if confidence is not None and confidence < 0.5 and avg_no_speech is not None and avg_no_speech > 0.3:
            return "low_quality"

        # High noise environment (even with speech)
        if avg_no_speech is not None and avg_no_speech > 0.4:
            return "low_quality"

        # Real speech: good confidence or clear speech
        if total_duration > 0.5:
            return "real_speech"

        return "real_speech"  # Default to real speech if we got text

    def _extract_legitimate_text(self, text: str) -> Optional[str]:
        """Extract legitimate request from mixed content (legitimate + metadata).

        Removes known metadata patterns while preserving the actual user request.

        Examples:
        - "Bonjour, pouvez-vous m'aider? Sous-titres réalisés par..."
          → "Bonjour, pouvez-vous m'aider?"
        - "Comment ça marche? YouTube subtitles by..."
          → "Comment ça marche?"
        """
        if not text:
            return ""  # Return empty string instead of None for consistency

        import re

        # Metadata separators - patterns that indicate where metadata starts
        # These patterns match: punctuation + whitespace + metadata keyword
        separators = [
            r'[.!?]\s+Sous-titres\b',                       # ". Sous-titres..."
            r'[.!?]\s+Subtitles\b',                         # ". Subtitles..."
            r'[.!?]\s+(?:par|by)\s+la\s+communauté',        # ". par la communauté"
            r'[.!?]\s+(?:Radio-Canada|YouTube|Netflix|Amara)', # Platform sources
            r'[.!?]\s+SousTitreur\.com',                     # Subtitle tool
            r'[.!?]\s+Sous-titrage\s+Société',              # Subtitle society
            r'[.!?]\s+Subtitles\s+(?:by|réalisés)',         # Generic subtitles credits
        ]

        # Try each separator pattern
        for separator_pattern in separators:
            match = re.search(separator_pattern, text, re.IGNORECASE)
            if match:
                # Find the punctuation before metadata
                # match.group(0) is something like ". Sous-titres"
                # We want to keep everything BEFORE this pattern
                matched_str = match.group(0)
                punct_char = matched_str[0]  # Get the punctuation character (., !, or ?)

                # Get text BEFORE the metadata separator
                legitimate = text[:match.start()].strip()

                # Make sure what's left is actually meaningful
                meaningful_chars = sum(1 for c in legitimate if c.isalnum() or c in "àâäéèêëïîôöûüçœæ'-")
                if meaningful_chars >= 3:  # At least 3 meaningful characters
                    # Add the ending punctuation back
                    legitimate_with_punct = legitimate + punct_char
                    logger.debug(f"[STT] Extracted legitimate text: '{legitimate_with_punct}' (removed metadata)")
                    return legitimate_with_punct

        # If no metadata separator found, return original
        return text

    def is_junk_transcription(self, text: str, confidence: float = None) -> bool:
        """Detect if transcription is junk/metadata that should be skipped.

        Returns True if text appears to be:
        - Metadata or credits (subtitles, credits, URLs)
        - Too short or only symbols/emojis
        - Known junk patterns (AFTER removing extractable metadata)

        Args:
            text: The transcribed text to validate
            confidence: Optional STT confidence score (0.0-1.0) for enhanced detection
        """
        if not text or not text.strip():
            return True

        # First, try to extract legitimate text from mixed content
        cleaned_text = self._extract_legitimate_text(text)
        if not cleaned_text:
            return True

        text_lower = cleaned_text.lower().strip()

        # Get junk confidence score (0.0 = legitimate, 1.0 = pure junk)
        junk_score = self._calculate_junk_score(text_lower, confidence)

        # Threshold: classify as junk if score >= 0.7
        # This allows for better nuance and reduces false positives
        is_junk = junk_score >= 0.7

        if is_junk:
            logger.debug(f"[STT] Detected junk (score: {junk_score:.2f}): '{cleaned_text[:80]}'")
        else:
            logger.debug(f"[STT] Legitimate text (score: {junk_score:.2f}): '{cleaned_text[:80]}'")

        return is_junk

    def calculate_utterance_quality_score(
        self,
        text: str,
        confidence: Optional[float] = None,
        speech_ratio: Optional[float] = None,
        noise_level: Optional[float] = None,
        audio_duration_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Calculate a comprehensive utterance quality score (0.0-1.0).

        Combines multiple factors into a single normalized score:
        1. Utterance length (normalized) - 0.25 weight
        2. Interrogative markers - 0.15 weight
        3. Intent confidence (STT) - 0.20 weight
        4. Speech ratio & noise level - 0.20 weight
        5. Semantic richness (token count) - 0.20 weight

        Args:
            text: Transcribed text
            confidence: STT confidence score (0.0-1.0)
            speech_ratio: Ratio of speech vs. silence (0.0-1.0)
            noise_level: Avg no-speech probability (0.0-1.0, lower is better)
            audio_duration_sec: Audio duration in seconds

        Returns:
            {
                "quality_score": float (0.0-1.0),
                "breakdown": {
                    "length": {...},
                    "interrogative": {...},
                    "confidence": {...},
                    "audio_metrics": {...},
                    "semantic_richness": {...}
                },
                "is_junk": bool (True if quality_score < 0.3),
                "flags": [...] (warnings/issues detected)
            }
        """
        text_lower = text.lower().strip()
        breakdown = {}
        flags = []

        # ═══════════════════════════════════════════════════════════════
        # FACTOR 1: UTTERANCE LENGTH (0.25 weight)
        # ═══════════════════════════════════════════════════════════════
        meaningful_chars = sum(
            1 for c in text_lower
            if c.isalnum() or c in "àâäéèêëïîôöûüçœæ'-"
        )
        token_count = len(text.split())

        # Normalized length score
        if meaningful_chars == 0:
            length_score = 0.0
            flags.append("empty_text")
        elif meaningful_chars <= 2:
            length_score = 0.1  # Very short but might be valid (Oui, Non, Aide)
            flags.append("very_short")
        elif meaningful_chars <= 5:
            length_score = 0.4  # Short (1-2 words)
        elif meaningful_chars <= 20:
            length_score = 0.7  # Medium (3-5 words)
        elif meaningful_chars <= 100:
            length_score = 0.9  # Long
        else:
            length_score = 1.0  # Very long

        breakdown["length"] = {
            "score": length_score,
            "weight": 0.25,
            "chars": meaningful_chars,
            "tokens": token_count,
            "category": (
                "very_short" if meaningful_chars <= 2 else
                "short" if meaningful_chars <= 5 else
                "medium" if meaningful_chars <= 20 else
                "long" if meaningful_chars <= 100 else
                "very_long"
            )
        }

        # ═══════════════════════════════════════════════════════════════
        # FACTOR 2: INTERROGATIVE MARKERS (0.15 weight)
        # ═══════════════════════════════════════════════════════════════
        has_question_mark = "?" in text
        interrogative_words_fr = [
            "quoi", "qui", "où", "quand", "pourquoi", "comment",
            "est-ce", "c'est quoi", "qu'est-ce", "quel", "laquelle"
        ]
        interrogative_words_en = [
            "what", "who", "where", "when", "why", "how",
            "is", "does", "can", "will", "should", "which"
        ]

        interrogative_score = 0.0
        interrogative_count = 0

        if has_question_mark:
            interrogative_score = 0.8
            interrogative_count += 1

        for word in interrogative_words_fr + interrogative_words_en:
            if word in text_lower:
                interrogative_score = max(interrogative_score, 0.7)
                interrogative_count += 1
                break  # Count presence, not frequency

        breakdown["interrogative"] = {
            "score": interrogative_score,
            "weight": 0.15,
            "has_question_mark": has_question_mark,
            "interrogative_keywords": interrogative_count > 0,
            "is_likely_question": interrogative_score > 0.5
        }

        # ═══════════════════════════════════════════════════════════════
        # FACTOR 3: STT CONFIDENCE (0.20 weight)
        # ═══════════════════════════════════════════════════════════════
        confidence_score = 0.5  # Default if not provided
        if confidence is not None:
            confidence_score = max(0.0, min(1.0, confidence))

        breakdown["confidence"] = {
            "score": confidence_score,
            "weight": 0.20,
            "stt_confidence": confidence,
            "interpretation": (
                "very_low" if confidence_score < 0.3 else
                "low" if confidence_score < 0.6 else
                "medium" if confidence_score < 0.8 else
                "high"
            )
        }

        # ═══════════════════════════════════════════════════════════════
        # FACTOR 4: AUDIO METRICS (Speech Ratio + Noise) (0.20 weight)
        # ═══════════════════════════════════════════════════════════════
        audio_score = 0.5  # Default if not provided

        if speech_ratio is not None and noise_level is not None:
            # Normalize both to 0-1
            speech_score = max(0.0, min(1.0, speech_ratio))
            # Lower noise is better, so invert it
            audio_quality = max(0.0, min(1.0, 1.0 - noise_level))
            # Combine: 60% speech ratio, 40% audio quality
            audio_score = (speech_score * 0.6) + (audio_quality * 0.4)

        breakdown["audio_metrics"] = {
            "score": audio_score,
            "weight": 0.20,
            "speech_ratio": speech_ratio,
            "noise_level": noise_level,
            "audio_quality": (
                "very_noisy" if (noise_level and noise_level > 0.7) else
                "noisy" if (noise_level and noise_level > 0.5) else
                "clean" if (noise_level and noise_level < 0.3) else
                "unknown"
            )
        }

        if audio_duration_sec:
            breakdown["audio_metrics"]["duration_sec"] = audio_duration_sec

        # ═══════════════════════════════════════════════════════════════
        # FACTOR 5: SEMANTIC RICHNESS (0.20 weight)
        # ═══════════════════════════════════════════════════════════════
        # Token count as proxy for semantic richness
        if token_count == 0:
            semantic_score = 0.0
        elif token_count == 1:
            semantic_score = 0.3  # Single word
        elif token_count <= 3:
            semantic_score = 0.6  # 2-3 words
        elif token_count <= 10:
            semantic_score = 0.8  # 4-10 words
        else:
            semantic_score = 1.0  # 10+ words

        breakdown["semantic_richness"] = {
            "score": semantic_score,
            "weight": 0.20,
            "token_count": token_count,
            "richness_category": (
                "none" if token_count == 0 else
                "minimal" if token_count == 1 else
                "low" if token_count <= 3 else
                "medium" if token_count <= 10 else
                "high"
            )
        }

        # ═══════════════════════════════════════════════════════════════
        # CALCULATE FINAL WEIGHTED SCORE
        # ═══════════════════════════════════════════════════════════════
        quality_score = (
            (breakdown["length"]["score"] * breakdown["length"]["weight"]) +
            (breakdown["interrogative"]["score"] * breakdown["interrogative"]["weight"]) +
            (breakdown["confidence"]["score"] * breakdown["confidence"]["weight"]) +
            (breakdown["audio_metrics"]["score"] * breakdown["audio_metrics"]["weight"]) +
            (breakdown["semantic_richness"]["score"] * breakdown["semantic_richness"]["weight"])
        )

        # Normalize to 0.0-1.0
        quality_score = max(0.0, min(1.0, quality_score))

        # ═══════════════════════════════════════════════════════════════
        # DETECT JUNK & FLAGS
        # ═══════════════════════════════════════════════════════════════
        is_junk = quality_score < 0.3

        # Additional junk detection patterns
        pure_junk_starters = [
            "sous-titres", "subtitles", "sous-titrage", "captioning",
            "crédits", "credits", "réalisé par", "realized by",
            "[silence]", "[inaudible]", "[music]", "[noise]",
        ]
        
        # Enhanced: Check if text contains common metadata patterns (not just starts with)
        junk_patterns = [
            "radio-canada", "société radio-canada", "amara.org",
            "youtube", "netflix", "sous-titreur",
        ]

        for starter in pure_junk_starters:
            if text_lower.startswith(starter):
                is_junk = True
                flags.append(f"junk_starter_{starter}")
        
        # Check for metadata anywhere in text
        for pattern in junk_patterns:
            if pattern in text_lower:
                is_junk = True
                flags.append(f"junk_pattern_{pattern}")

        # Log the complete breakdown
        logger.info(
            f"[STT Quality Score] {quality_score:.2f} | "
            f"length={breakdown['length']['score']:.2f} ({breakdown['length']['category']}) | "
            f"question={breakdown['interrogative']['score']:.2f} ({breakdown['interrogative']['is_likely_question']}) | "
            f"confidence={breakdown['confidence']['score']:.2f} ({breakdown['confidence']['interpretation']}) | "
            f"audio={breakdown['audio_metrics']['score']:.2f} ({breakdown['audio_metrics']['audio_quality']}) | "
            f"semantic={breakdown['semantic_richness']['score']:.2f} ({breakdown['semantic_richness']['richness_category']}) | "
            f"junk={is_junk} | text='{text[:60]}'"
        )

        return {
            "quality_score": quality_score,
            "breakdown": breakdown,
            "is_junk": is_junk,
            "flags": flags,
        }

    def _calculate_junk_score(self, text_lower: str, confidence: float = None) -> float:
        """
        DEPRECATED: Use calculate_utterance_quality_score() instead.

        Legacy junk detection method - kept for backward compatibility.
        Returns a junk score (0.0 = legitimate, 1.0 = pure junk).
        """
        # For backward compatibility, convert quality score to junk score
        result = self.calculate_utterance_quality_score(text_lower, confidence=confidence)
        junk_score = 1.0 - result["quality_score"]  # Invert: low quality = high junk
        logger.debug(f"[STT Scoring DEPRECATED] Legacy junk_score: {junk_score:.3f}")
        return junk_score


def get_stt_service() -> STTService:
    return STTService()

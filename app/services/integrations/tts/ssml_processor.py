"""SSML Processor for natural-sounding TTS.

Transforms plain text into SSML with pauses, emphasis, and prosody
for more human-like voice output.

NATURALNESS ENHANCEMENTS:
- Breath pauses between long sentences (simulates human breathing)
- Filler word prosody (slower pace on "hmm", "alors", "attends")
- Dynamic pause lengths based on sentence complexity
- Softer pitch contours for statement endings
- Intent-aware processing for empathy, alerts, and support
"""
import re
from typing import Optional
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class SSMLProcessor:
    """Transform plain text into natural-sounding SSML.
    
    Adds:
    - Pauses after sentences and commas
    - Breath pauses between long sentences
    - Filler word prosody adjustments
    - Emphasis on numbers, prices, and brand names
    - Prosody control for questions and statements
    - Intent-aware emotional coloring
    
    Note: ElevenLabs supports a subset of SSML. Main tags:
    - <break time="Xms"/> for pauses
    - <emphasis> for stress (limited support)
    - <prosody rate="X%" pitch="X%"> for speed/pitch
    """
    
    # Pause durations in milliseconds - TUNED FOR NATURAL SPEECH
    PAUSE_SENTENCE = 450       # After . ! ? - slightly longer for breath
    PAUSE_SENTENCE_LONG = 550  # After long sentences (>60 chars) - breath pause
    PAUSE_COMMA = 180          # After , - quick breath
    PAUSE_COLON = 350          # After : - anticipation pause
    PAUSE_ELLIPSIS = 600       # After ... - thinking/hesitation
    PAUSE_BREATH = 250         # Between clauses - simulates breathing
    
    # Filler words that should be spoken slower (more human-like)
    FILLER_WORDS_FR = ["hmm", "euh", "alors", "attends", "bon", "bah", "enfin", "donc"]
    FILLER_WORDS_EN = ["hmm", "uh", "well", "so", "um", "okay", "right", "look"]
    
    def __init__(self, enable_ssml: bool = True):
        """Initialize processor.
        
        Args:
            enable_ssml: If False, return text unchanged (for debugging)
        """
        self.enable_ssml = enable_ssml
    
    def enhance(self, text: str, language: str = "fr", intent_type: Optional[str] = None) -> str:
        """Transform text into SSML with natural speech patterns.
        
        Args:
            text: Plain text to enhance
            language: Language code (fr/en) for language-specific rules
            intent_type: Optional intent for emotional coloring
            
        Returns:
            SSML-enhanced text ready for TTS
        """
        if not self.enable_ssml or not text.strip():
            return text
        
        try:
            result = text
            
            # 1. Add filler word prosody first (before other transformations)
            result = self._add_filler_word_prosody(result, language)
            
            # 2. Add pauses after punctuation (with dynamic lengths)
            result = self._add_sentence_pauses_dynamic(result)
            result = self._add_comma_pauses(result)
            result = self._add_colon_pauses(result)
            result = self._add_ellipsis_pauses(result)
            
            # 3. Add breath pauses between long clauses
            result = self._add_breath_pauses(result, language)
            
            # 4. Emphasize important elements
            result = self._emphasize_numbers(result, language)

            # 5. Add prosody for questions (rising) and statements (falling)
            result = self._add_question_prosody(result, language)
            result = self._add_statement_ending_prosody(result)
            
            # 6. Apply intent-specific coloring
            if intent_type:
                result = self._apply_intent_coloring(result, intent_type)
            
            # 7. Wrap in speak tags
            result = f"<speak>{result}</speak>"
            
            logger.debug(f"SSML enhanced ({intent_type or 'neutral'}): '{text[:40]}...'")
            return result
            
        except Exception as e:
            logger.warning(f"SSML enhancement failed, returning plain text: {e}")
            return text
    
    def _add_filler_word_prosody(self, text: str, language: str) -> str:
        """Add slower prosody to filler words for natural hesitation.
        
        Filler words like "hmm", "alors", "attends" should be spoken slower,
        as humans naturally slow down on these thinking words.
        """
        fillers = self.FILLER_WORDS_FR if language == "fr" else self.FILLER_WORDS_EN
        result = text
        
        for filler in fillers:
            # Match filler word at word boundary, case insensitive
            # Add slight slowdown and pause after
            pattern = rf'\b({re.escape(filler)})\b'
            replacement = rf'<prosody rate="90%">\1</prosody><break time="150ms"/>'
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        
        return result
    
    def _add_sentence_pauses_dynamic(self, text: str) -> str:
        """Add dynamic pauses after sentences based on length.
        
        Longer sentences get longer pauses (simulates catching breath).
        """
        # Split into sentences and reassemble with appropriate pauses
        sentences = re.split(r'([.!?])(\s+|$)', text)
        result_parts = []
        
        i = 0
        while i < len(sentences):
            if i >= len(sentences):
                break
                
            part = sentences[i]
            
            # Check if next part is punctuation
            if i + 1 < len(sentences) and sentences[i + 1] in '.!?':
                sentence_text = part
                punctuation = sentences[i + 1]
                spacing = sentences[i + 2] if i + 2 < len(sentences) else ''
                
                # Dynamic pause based on sentence length
                if len(sentence_text) > 80:
                    pause = self.PAUSE_SENTENCE_LONG  # Long sentence - longer breath
                elif len(sentence_text) > 40:
                    pause = self.PAUSE_SENTENCE
                else:
                    pause = 350  # Short sentence - quicker pace
                
                result_parts.append(f'{sentence_text}{punctuation}<break time="{pause}ms"/>{spacing}')
                i += 3
            else:
                result_parts.append(part)
                i += 1
        
        return ''.join(result_parts)
    
    def _add_breath_pauses(self, text: str, language: str) -> str:
        """Add subtle breath pauses between clauses for natural rhythm.
        
        Humans breathe between thoughts. This adds micro-pauses after
        conjunction words to simulate that breathing pattern.
        """
        # Conjunction patterns that often precede natural breath pauses
        if language == "fr":
            conjunctions = r'\b(mais|et|ou|donc|parce que|car|puis|ensuite)\b'
        else:
            conjunctions = r'\b(but|and|or|so|because|then|also|however)\b'
        
        # Add tiny pause after conjunctions (only if followed by space)
        pattern = rf'({conjunctions})(\s+)'
        replacement = rf'\1<break time="{self.PAUSE_BREATH}ms"/>\2'
        return re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    def _add_comma_pauses(self, text: str) -> str:
        """Add short pauses after commas."""
        pattern = r',(\s+)'
        replacement = rf',<break time="{self.PAUSE_COMMA}ms"/>\1'
        return re.sub(pattern, replacement, text)
    
    def _add_colon_pauses(self, text: str) -> str:
        """Add pauses after colons (before lists/explanations)."""
        pattern = r':(\s+)'
        replacement = rf':<break time="{self.PAUSE_COLON}ms"/>\1'
        return re.sub(pattern, replacement, text)
    
    def _add_ellipsis_pauses(self, text: str) -> str:
        """Add longer pauses for ellipsis (thinking/hesitation)."""
        pattern = r'\.\.\.(\s*)'
        replacement = rf'<break time="{self.PAUSE_ELLIPSIS}ms"/>\1'
        return re.sub(pattern, replacement, text)
    
    def _emphasize_numbers(self, text: str, language: str) -> str:
        """Emphasize numbers, prices, and percentages.
        
        Numbers are often important (prices, doses, dates).
        """
        # Match prices (€, $), percentages, and time expressions
        price_pattern = r'(\d+[.,]?\d*\s*[€$%]|\d+[.,]?\d*\s*euros?|\d+[.,]?\d*\s*dollars?|\d{1,2}[h:]\d{2})'
        
        def emphasize_match(match):
            return f'<prosody rate="92%">{match.group(0)}</prosody>'
        
        return re.sub(price_pattern, emphasize_match, text, flags=re.IGNORECASE)
    
    def _add_question_prosody(self, text: str, language: str) -> str:
        """Add rising intonation for questions."""
        pattern = r'(\?)'
        replacement = r'<prosody pitch="+8%">\1</prosody>'
        return re.sub(pattern, replacement, text)
    
    def _add_statement_ending_prosody(self, text: str) -> str:
        """Add slight pitch drop at end of statements for natural finality.
        
        Statements naturally end with falling intonation.
        """
        # Add slight pitch drop before periods (not covered by question prosody)
        pattern = r'(\.)(<break)'
        replacement = r'<prosody pitch="-3%">\1</prosody>\2'
        return re.sub(pattern, replacement, text)
    
    def _apply_intent_coloring(self, text: str, intent_type: str) -> str:
        """Apply intent-specific SSML coloring.
        
        Different intents require different emotional tones:
        - Empathy: softer, slightly slower overall
        - Alerts: clearer, more direct
        - Success: warmer, slightly upbeat
        """
        # Wrap entire text with intent-appropriate prosody
        if intent_type in ["empathy", "concern", "apology", "patient_care"]:
            # Softer, more comforting tone
            return f'<prosody rate="97%" pitch="-2%">{text}</prosody>'
        elif intent_type in ["alert", "urgent", "missed_dose", "reminder"]:
            # Clear, focused tone
            return f'<prosody rate="102%" pitch="+1%">{text}</prosody>'
        elif intent_type in ["success", "confirmation", "resolution"]:
            # Warm, positive tone
            return f'<prosody pitch="+3%">{text}</prosody>'
        
        return text
    
    def strip_ssml(self, text: str) -> str:
        """Remove all SSML tags to get plain text.
        
        Useful for logging or fallback.
        """
        return re.sub(r'<[^>]+>', '', text)


def get_ssml_processor(enable: bool = True) -> SSMLProcessor:
    """Factory for SSMLProcessor."""
    return SSMLProcessor(enable_ssml=enable)

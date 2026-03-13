"""Decode steganographic text back to angle index."""
from typing import Any, Dict, List, Optional

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config


class DecodePipeline:
    """Pipeline for decoding stego text to angle index."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self.config = get_config()
    
    def decode(
        self,
        stego_text: str,
        angles: List[Dict[str, Any]],
        few_shots: Optional[List[Dict[str, Any]]] = None,
        base_url: Optional[str] = None,
    ) -> Optional[int]:
        """
        Decode stego text to angle index.
        
        Args:
            stego_text: Steganographic text to decode
            angles: List of angle dictionaries
            few_shots: Optional few-shot examples
            base_url: Base URL for API calls
        
        Returns:
            Decoded angle index (0-based) or None if decoding fails
        """
        if not angles:
            return None
        
        # Use semantic search to find best matching angle
        try:
            search_result = self.backend.semantic_search(
                text=stego_text,
                objects=angles,
                n=5,  # Get top 5 matches
            )
            
            results = search_result.get("results", [])
            if not results:
                return None
            
            # Build few-shot prompt
            few_shot_text = ""
            if few_shots:
                few_shot_examples = []
                for shot in few_shots[:3]:  # Limit to 3 examples
                    text = shot.get("texts", [])
                    if isinstance(text, list) and text:
                        few_shot_examples.append(text[0])
                if few_shot_examples:
                    few_shot_text = "\n\nExamples:\n" + "\n".join(
                        f"- {ex}" for ex in few_shot_examples
                    )
            
            # Build prompt for LLM
            top_matches = "\n".join(
                f"{i+1}. {r['object'].get('tangent', '')[:100]}"
                for i, r in enumerate(results[:5])
            )
            
            prompt = f"""Given the following steganographic text, determine which angle index (0-{len(angles)-1}) it corresponds to.

Stego text:
{stego_text}

Top matching angles from semantic search:
{top_matches}
{few_shot_text}

Return ONLY the integer index (0-{len(angles)-1}) that best matches the stego text. No explanation, just the number."""
            
            system_message = "You are a steganographic decoder. Return only the integer index that matches the stego text."
            
            # Call LLM
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )
            
            # Parse index from response
            try:
                # Try to extract integer from response
                import re
                numbers = re.findall(r'\d+', response.strip())
                if numbers:
                    idx = int(numbers[0])
                    if 0 <= idx < len(angles):
                        return idx
            except (ValueError, IndexError):
                pass
            
            # Fallback: use semantic search top result
            if results:
                # Find the index in original angles list
                top_result = results[0]["object"]
                for i, angle in enumerate(angles):
                    if (
                        angle.get("tangent") == top_result.get("tangent")
                        and angle.get("source_quote") == top_result.get("source_quote")
                    ):
                        return i
            
            return None
            
        except Exception as e:
            print(f"Error decoding stego text: {e}")
            return None

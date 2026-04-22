"""
Dynamic Template-based description generation service.
Handles any product type with flexible attribute mapping.
"""

import logging
import random
from typing import Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class TemplateService:
    """
    Dynamic template service that can generate descriptions for any product type
    based on scraped attributes, not just predefined categories.
    """
    
    def __init__(self):
        self.translation_mapping = self._load_translation_mapping()
        self.template_patterns = self._load_template_patterns()
    
    def _load_translation_mapping(self) -> Dict[str, str]:
        """Load translation mapping from file"""
        mapping = {}
        try:
            with open("translate_mapping.txt", "r", encoding="utf-8-sig") as f:
                for line in f:
                    if ":" in line:
                        key, value = line.strip().split(":", 1)
                        mapping[key.strip().upper()] = value.strip()
        except Exception as e:
            logger.error(f"Failed to load translation mapping: {e}")
        return mapping
    
    def _load_template_patterns(self) -> Dict[str, List[str]]:
        """Load dynamic template patterns"""
        return {
            "opening": [
                "Premium {material} {product_type} in {color} kleur.",
                "Elegante {material} {product_type} in {color}.",
                "Stijlvolle {material} {product_type} in {color} kleur.",
                "Moderne {material} {product_type} in {color}.",
                "Hoge kwaliteit {material} {product_type} in {color} kleur."
            ],
            "features": [
                "Met {collar} kraag en {fit} pasvorm voor optimaal comfort.",
                "Kenmerkt een {collar} kraag met {fit} pasvorm voor ultiem comfort.",
                "Met {collar} kraag en {fit} pasvorm voor dagelijks comfort.",
                "Kenmerkt een {collar} kraag met {fit} pasvorm voor optimaal draagcomfort.",
                "Met {collar} kraag en {fit} pasvorm voor perfect comfort."
            ],
            "use_cases": [
                "Perfect voor dagelijks gebruik en speciale gelegenheden.",
                "Ideal voor casual en business casual gelegenheden.",
                "Perfect voor professionele omgevingen en formele gelegenheden.",
                "Ideal voor ontspannen momenten en dagelijks gebruik.",
                "Perfect voor elke gelegenheid en speciale momenten."
            ],
            "quality": [
                "Hoge kwaliteit constructie zorgt voor duurzaamheid en stijl.",
                "Superieure kwaliteit en vakmanschap.",
                "Uitstekende kwaliteit en moderne uitstraling.",
                "Hoge kwaliteit en duurzaamheid.",
                "Premium kwaliteit en superieure vakmanschap."
            ]
        }
    
    def generate_description(self, product_data: Dict[str, Any], product_name: str) -> str:
        """Generate dynamic description for any product type"""
        
        # Extract and translate attributes
        attributes = self._extract_attributes(product_data)
        
        # Determine product type dynamically
        product_type = self._determine_product_type_dynamic(attributes, product_name)
        
        # Generate description using dynamic patterns
        return self._generate_dynamic_description(attributes, product_type)
    
    def _extract_attributes(self, product_data: Dict[str, Any]) -> Dict[str, str]:
        """Extract and translate all relevant attributes"""
        attributes = {}
        
        # Map common attribute names
        attribute_mapping = {
            'quality': ['quality', 'materiaal', 'fabriccomp', 'material'],
            'color': ['color', 'kleur'],
            'fit': ['fit', 'pasvorm', 'model'],
            'collar': ['collar', 'kraag'],
            'sleeve': ['sleeve', 'mouwen', 'mouwlengte'],
            'design': ['design', 'patroon'],
            'sustainability': ['sustainability', 'duurzaamheid'],
            'noniron': ['noniron', 'strijkvrij']
        }
        
        for attr_name, possible_keys in attribute_mapping.items():
            for key in possible_keys:
                if key in product_data and product_data[key]:
                    attributes[attr_name] = self.translate_to_dutch(str(product_data[key]))
                    break
            else:
                # Set default values
                defaults = {
                    'quality': 'katoen',
                    'color': 'klassiek',
                    'fit': 'comfortabel',
                    'collar': 'elegant',
                    'sleeve': 'normale mouw',
                    'design': 'effen',
                    'sustainability': '',
                    'noniron': ''
                }
                attributes[attr_name] = defaults.get(attr_name, '')
        
        return attributes
    
    def _determine_product_type_dynamic(self, attributes: Dict[str, str], product_name: str) -> str:
        """Dynamically determine product type from attributes and name"""
        
        # Check product name for clues
        name_lower = product_name.lower()
        
        # Define product type keywords
        product_types = {
            'polo': ['polo', 'polo shirt'],
            'shirt': ['shirt', 'overhemd', 'hemd'],
            'sweater': ['sweater', 'trui', 'pullover', 'jumper', 'cardigan', 'shawl cardigan'],
            'jacket': ['jacket', 'jas', 'blazer'],
            'trousers': ['trousers', 'pants', 'broek', 'pantalon'],
            'shoes': ['shoes', 'schoenen', 'sneakers'],
            'accessories': ['tie', 'das', 'belt', 'riem', 'accessory']
        }
        
        # Check name first
        for ptype, keywords in product_types.items():
            if any(keyword in name_lower for keyword in keywords):
                return ptype
        
        # Check attributes for clues
        collar = attributes.get('collar', '').lower()
        sleeve = attributes.get('sleeve', '').lower()
        
        if 'polo' in collar:
            return 'polo'
        elif 'long' in sleeve or 'lange' in sleeve:
            return 'shirt'
        elif 'short' in sleeve or 'korte' in sleeve:
            return 'shirt'
        elif 'sweater' in collar or 'trui' in collar:
            return 'sweater'
        
        # Default fallback - try to be more generic
        return 'kledingstuk'
    
    def _generate_dynamic_description(self, attributes: Dict[str, str], product_type: str) -> str:
        """Generate description using dynamic patterns"""
        
        # Get product type in Dutch
        product_type_dutch = self._get_product_type_dutch(product_type)
        
        # Select random patterns for variety
        opening = random.choice(self.template_patterns["opening"]).format(
            material=attributes.get('quality', 'katoen'),
            product_type=product_type_dutch,
            color=attributes.get('color', 'klassiek')
        )
        
        # Add features if available
        features = ""
        if attributes.get('collar') and attributes.get('fit'):
            features = " " + random.choice(self.template_patterns["features"]).format(
                collar=attributes.get('collar', 'elegant'),
                fit=attributes.get('fit', 'comfortabel')
            )
        
        # Add use case
        use_case = " " + random.choice(self.template_patterns["use_cases"])
        
        # Add quality statement
        quality = " " + random.choice(self.template_patterns["quality"])
        
        # Combine and clean up
        description = opening + features + use_case + quality
        description = self._clean_description(description)
        
        return description
    
    def _get_product_type_dutch(self, product_type: str) -> str:
        """Get Dutch translation for product type"""
        translations = {
            'polo': 'polo shirt',
            'shirt': 'shirt',
            'sweater': 'trui',
            'jacket': 'jas',
            'trousers': 'broek',
            'shoes': 'schoenen',
            'accessories': 'accessoire',
            'kledingstuk': 'kledingstuk'
        }
        return translations.get(product_type, 'kledingstuk')
    
    def translate_to_dutch(self, text: str) -> str:
        """Translate English terms to Dutch"""
        if not text or str(text) == 'nan':
            return ''
        
        text_upper = str(text).upper()
        
        # Direct match
        if text_upper in self.translation_mapping:
            return self.translation_mapping[text_upper]
        
        # Partial match
        for key, value in self.translation_mapping.items():
            if key in text_upper:
                return value
        
        return str(text).lower()
    
    def _clean_description(self, description: str) -> str:
        """Clean and format description"""
        import re
        
        # Remove extra whitespace
        description = re.sub(r'\s+', ' ', description).strip()
        
        # Ensure proper sentence structure
        if not description.endswith('.'):
            description += '.'
        
        # Limit to 160 characters
        if len(description) > 160:
            # Try to cut at sentence boundary
            sentences = description.split('.')
            result = ""
            for sentence in sentences:
                if len(result + sentence + '.') <= 160:
                    result += sentence + '.'
                else:
                    break
            description = result or description[:157] + '...'
        
        return description
    
    def generate_meta_description(self, product_data: Dict[str, Any], product_name: str) -> str:
        """Generate shorter meta description"""
        full_description = self.generate_description(product_data, product_name)
        
        # Create shorter version
        if len(full_description) <= 80:
            return full_description
        
        # Take first sentence if it fits
        sentences = full_description.split('.')
        if sentences and len(sentences[0]) <= 80:
            return sentences[0] + '.'
        
        # Truncate intelligently
        words = full_description.split()
        meta_desc = ""
        for word in words:
            if len(meta_desc + word) <= 77:
                meta_desc += word + " "
            else:
                break
        
        return meta_desc.strip() + "..."

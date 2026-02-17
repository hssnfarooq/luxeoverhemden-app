from automations.openai_service import OpenAIService
from automations.template_service import TemplateService
from config import OPENAI_API_KEY, OPENAI_MODEL

# Create template service as the main service
# This replaces the expensive OpenAI service with cost-effective template generation
template_service = TemplateService()

# Keep OpenAI service as backup (optional)
openai_service = OpenAIService(api_key=OPENAI_API_KEY, model=OPENAI_MODEL)

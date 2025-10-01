from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered
from marker.config.parser import ConfigParser
import marker
from dotenv import load_dotenv
import os

load_dotenv()

file_path = "./documents/input_med.pdf"

# Follow Marker docs: provide a config dict; LLM is optional and resolved by parser if use_llm=True.
config = {
    "output_format": "markdown",
    "use_llm": False, 
    "redo_inline_math": True,
    "llm_service" : "marker.services.openai.OpenAIService",
    "openai_api_key" : os.getenv("OPENAI_API_KEY"),
    "openai_model" : "gpt-5",
    "openai_base_url" : "https://api.openai.com/v1"
}

config_parser = ConfigParser(config)

converter = PdfConverter(
    config=config_parser.generate_config_dict(),
    artifact_dict=create_model_dict(),
    processor_list=config_parser.get_processors(),
    renderer=config_parser.get_renderer(),
    llm_service=config_parser.get_llm_service(),
)


if __name__ == "__main__":
    result = converter(file_path)
    text, _, images = text_from_rendered(result)

    out_path = 'output_pdf.md'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
        print(f"Wrote markdown to {out_path}")
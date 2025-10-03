from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered
from marker.config.parser import ConfigParser
import marker
from dotenv import load_dotenv
import os
from io import BytesIO
from pathlib import Path

load_dotenv()

PDF_PATH = str((Path(__file__).resolve().parent / "documents" / "one_pager.pdf"))
OUT_PATH = "output_pdf.md"


# Follow Marker docs: provide a config dict; LLM is optional and resolved by parser if use_llm=True.
config = {
    "output_format": "markdown",
    "use_llm": False, 
    "redo_inline_math": True,
    # "llm_service" : "marker.services.openai.OpenAIService",
    # "openai_api_key" : os.getenv("OPENAI_API_KEY"),
    # "openai_model" : "gpt-5",
    # "openai_base_url" : "https://api.openai.com/v1"
}

config_parser = ConfigParser(config)

converter = PdfConverter(
    config=config_parser.generate_config_dict(),
    artifact_dict=create_model_dict(),
    processor_list=config_parser.get_processors(),
    renderer=config_parser.get_renderer(),
    llm_service=config_parser.get_llm_service(),
)


def _render_to_markdown(result) -> str:
    text, _, _ = text_from_rendered(result)
    return text


def convert_pdf_path(path: str) -> str:
    """Convert a PDF located on disk to Markdown."""

    rendered = converter(path)
    return _render_to_markdown(rendered)


def convert_pdf_bytes(data: bytes) -> str:
    """Convert raw PDF bytes to Markdown using an in-memory buffer."""

    buffer = BytesIO(data)
    rendered = converter(buffer)
    return _render_to_markdown(rendered)


if __name__ == "__main__":
    result = converter(PDF_PATH)
    text, _, images = text_from_rendered(result)

    out_path = OUT_PATH
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
        print(f"Wrote markdown to {out_path}")

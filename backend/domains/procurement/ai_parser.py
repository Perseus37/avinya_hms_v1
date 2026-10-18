import re
import json
import torch
from PIL import Image
from transformers import DonutProcessor, VisionEncoderDecoderModel
from pdf2image import convert_from_path

class DonutInvoiceParser:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DonutInvoiceParser, cls).__new__(cls)
            cls._instance._initialize_model()
        return cls._instance

    def _initialize_model(self):
        # Using the base fine-tuned receipt/invoice model.
        # For production, replace with your custom fine-tuned weights path.
        model_name = "naver-clova-ix/donut-base-finetuned-cord-v2"
        self.processor = DonutProcessor.from_pretrained(model_name)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def _pdf_to_image(self, file_path: str) -> Image.Image:
        """Converts the first page of a PDF to a PIL Image."""
        if file_path.lower().endswith('.pdf'):
            images = convert_from_path(file_path, first_page=1, last_page=1)
            return images[0].convert("RGB")
        return Image.open(file_path).convert("RGB")

    def extract_invoice_data(self, file_path: str) -> dict:
        image = self._pdf_to_image(file_path)

        task_prompt = "<s_cord-v2>"
        decoder_input_ids = self.processor.tokenizer(
            task_prompt, add_special_tokens=False, return_tensors="pt"
        ).input_ids

        pixel_values = self.processor(image, return_tensors="pt").pixel_values

        outputs = self.model.generate(
            pixel_values.to(self.device),
            decoder_input_ids=decoder_input_ids.to(self.device),
            max_length=self.model.decoder.config.max_position_embeddings,
            pad_token_id=self.processor.tokenizer.pad_token_id,
            eos_token_id=self.processor.tokenizer.eos_token_id,
            use_cache=True,
            bad_words_ids=[[self.processor.tokenizer.unk_token_id]],
            return_dict_in_generate=True,
        )

        sequence = self.processor.batch_decode(outputs.sequences)[0]
        sequence = sequence.replace(self.processor.tokenizer.eos_token, "").replace(self.processor.tokenizer.pad_token, "")
        sequence = re.sub(r"<.*?>", "", sequence, count=1).strip()

        return self.processor.token2json(sequence)
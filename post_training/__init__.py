"""Post-training pipeline package.

Consumes the records collected by the data flywheel and produces
training-set files suitable for SFT / DPO post-training of the LLM.

This is intentionally a *generator* of training data, not a trainer itself:
the actual fine-tuning is delegated to an external platform (OpenAI, Zhipu,
LLaMA-Factory, ...). The pipeline just shapes the data and writes it to disk
under `POST_TRAIN_OUTPUT_DIR`.

Future expansion hooks:
  * instruction augmentation (paraphrase, chain-of-thought synthesis)
  * preference pair construction (chosen from good cases, rejected from bad)
  * train/eval split
  * direct upload to fine-tuning API
"""
from post_training.pipeline import PostTrainingPipeline
from post_training.quality import (
    evaluate_sft,
    evaluate_dpo,
    recommendations,
)

__all__ = [
    "PostTrainingPipeline",
    "evaluate_sft",
    "evaluate_dpo",
    "recommendations",
]

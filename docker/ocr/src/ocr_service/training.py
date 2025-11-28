"""TrOCR fine-tuning with LoRA."""

import json
import logging
from datetime import datetime
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model
from PIL import Image
from transformers import (
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    TrOCRProcessor,
    VisionEncoderDecoderModel,
)

logger = logging.getLogger(__name__)


def run_training(
    dataset_version: str,
    output_version: str,
    base_model: str = "microsoft/trocr-base-handwritten",
    epochs: int = 3,
    batch_size: int = 8,
    learning_rate: float = 5e-5,
    use_lora: bool = True,
    data_dir: Path | None = None,
    checkpoints_dir: Path | None = None,
):
    """Run fine-tuning on correction dataset.

    Args:
        dataset_version: Version of dataset to train on
        output_version: Version string for output model
        base_model: Base model to fine-tune
        epochs: Number of training epochs
        batch_size: Training batch size
        learning_rate: Learning rate
        use_lora: Whether to use LoRA for efficient fine-tuning
        data_dir: Directory containing datasets (default: /app/data for container)
        checkpoints_dir: Directory for checkpoints (default: /app/checkpoints for container)
    """
    logger.info(f"Starting training: dataset={dataset_version}, output={output_version}")

    # Paths (configurable for testing, defaults for container deployment)
    data_dir = data_dir or Path("/app/data")
    checkpoints_dir = checkpoints_dir or Path("/app/checkpoints")
    output_dir = checkpoints_dir / output_version

    dataset_path = data_dir / f"{dataset_version}.parquet"
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    # Load processor and model
    processor = TrOCRProcessor.from_pretrained(base_model)
    model = VisionEncoderDecoderModel.from_pretrained(base_model)

    # Configure LoRA if enabled
    if use_lora:
        logger.info("Configuring LoRA for efficient fine-tuning")
        lora_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj", "k_proj", "out_proj"],
            lora_dropout=0.1,
            bias="none",
            task_type=TaskType.SEQ_2_SEQ_LM,
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

    # Load dataset
    dataset = load_dataset("parquet", data_files=str(dataset_path))

    # Preprocessing function
    def preprocess(examples):
        images = []
        for img_path in examples["image_path"]:
            image = Image.open(img_path).convert("RGB")
            images.append(image)

        pixel_values = processor(images=images, return_tensors="pt").pixel_values

        labels = processor.tokenizer(
            examples["corrected_text"],
            padding="max_length",
            max_length=128,
            truncation=True,
            return_tensors="pt",
        ).input_ids

        # Replace padding token id with -100 so it's ignored in loss
        labels[labels == processor.tokenizer.pad_token_id] = -100

        return {
            "pixel_values": pixel_values,
            "labels": labels,
        }

    # Process dataset with batching to avoid OOM on large datasets
    processed_dataset = dataset["train"].map(
        preprocess,
        batched=True,
        batch_size=32,  # Process in chunks to limit memory usage
        remove_columns=dataset["train"].column_names,
    )

    # Split into train/eval
    split_dataset = processed_dataset.train_test_split(test_size=0.1)

    # Training arguments
    training_args = Seq2SeqTrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=0.01,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        predict_with_generate=True,
        fp16=torch.cuda.is_available(),
        report_to="none",
    )

    # Create trainer
    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=split_dataset["train"],
        eval_dataset=split_dataset["test"],
        tokenizer=processor.tokenizer,
    )

    # Train
    logger.info("Starting training...")
    train_result = trainer.train()

    # Save model
    logger.info(f"Saving model to {output_dir}")
    if use_lora:
        # Save LoRA adapter
        model.save_pretrained(output_dir)
    else:
        # Save full model
        trainer.save_model(output_dir)

    # Calculate metrics (simplified CER/WER)
    eval_results = trainer.evaluate()
    metrics = {
        "eval_loss": eval_results.get("eval_loss", 0),
        "train_loss": train_result.training_loss,
        "epochs": epochs,
        "samples": len(processed_dataset),
    }

    # Save metadata
    metadata = {
        "version": output_version,
        "base_model": base_model,
        "dataset_version": dataset_version,
        "created_at": datetime.now().isoformat(),
        "metrics": metrics,
        "use_lora": use_lora,
    }

    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Training complete! Metrics: {metrics}")
    return metrics


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--no-lora", action="store_true")

    args = parser.parse_args()

    run_training(
        dataset_version=args.dataset,
        output_version=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        use_lora=not args.no_lora,
    )

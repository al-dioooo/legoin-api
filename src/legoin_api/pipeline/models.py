"""The real pipeline: legoin-ml release v0.2.0, exactly as its release notes say.

1. Detect with YOLO11n (`best.pt`, Ultralytics 8.4.190) on the upright RGB photo, at
   `imgsz` 640, `conf` 0.65, `iou` 0.7 and `max_det` 300.
2. Crop each box widened the way Ultralytics' `save_one_box` does: 1.02× plus 10 px,
   clipped to the photo.
3. Part: letterbox the crop to 224 px on a (124, 116, 104) square, apply ImageNet
   normalization, then `softmax(logits / 0.7493)` over the 14 outputs. The part sent is the
   argmax of outputs 0–12, and its probability is the part confidence; `other` (13) is never
   sent. A piece that leans to `other` gets a confidence of at most 0.5, so it's flagged.
4. Color: notebook 03's method (`pipeline/color.py`) with `legoin_warna.json`.

The worker takes the lowest of the box, part and color scores as each piece's confidence, and
never drops a detected piece.

Needs the `models` extra.
"""

import json
import logging
import time
from pathlib import Path

import numpy as np
import torch
import torchvision
import torchvision.transforms.functional as TF
from PIL import Image, ImageOps
from ultralytics import YOLO

from legoin_api.catalogue import PART_IDS, ColorId, PartId
from legoin_api.pipeline import Found, Reading
from legoin_api.pipeline.color import ColorParameters, putuskan, warna_kepingan
from legoin_api.pipeline.release import (
    CONF,
    CROP_GAIN,
    CROP_PAD,
    IMGSZ,
    INPUT_SIZE,
    IOU,
    MAX_DET,
    MEAN,
    NUM_OUTPUTS,
    OTHER_INDEX,
    PAD_COLOR,
    STD,
    TEMPERATURE,
    check_label_map,
)
from legoin_api.weights import CLASSIFIER, CLASSIFIER_LABELS, COLOR_PARAMETERS, DETECTOR

log = logging.getLogger(__name__)


def widen(found: Found, width: int, height: int) -> tuple[int, int, int, int]:
    """The crop box `save_one_box` would cut, in float32 as Ultralytics computes it."""
    x1, y1, x2, y2 = np.array([found.x1, found.y1, found.x2, found.y2], dtype=np.float32)
    cx, cy = (x1 + x2) / np.float32(2), (y1 + y2) / np.float32(2)
    w = (x2 - x1) * np.float32(CROP_GAIN) + np.float32(CROP_PAD)
    h = (y2 - y1) * np.float32(CROP_GAIN) + np.float32(CROP_PAD)
    half_w, half_h = w / np.float32(2), h / np.float32(2)
    # `.long()` truncates toward zero, then the box is clipped to the photo.
    box = [int(v) for v in (cx - half_w, cy - half_h, cx + half_w, cy + half_h)]
    return (
        min(max(box[0], 0), width),
        min(max(box[1], 0), height),
        min(max(box[2], 0), width),
        min(max(box[3], 0), height),
    )


def crop(image: Image.Image, found: Found) -> Image.Image:
    return image.crop(widen(found, image.width, image.height))


def letterbox(im: Image.Image) -> Image.Image:
    """Notebook 02's `letterbox`: never stretch the crop, since 1×2 vs 1×4 is aspect ratio."""
    im = ImageOps.contain(im, (INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    canvas = Image.new("RGB", (INPUT_SIZE, INPUT_SIZE), PAD_COLOR)
    canvas.paste(im, ((INPUT_SIZE - im.width) // 2, (INPUT_SIZE - im.height) // 2))
    return canvas


class ModelPipeline:
    def __init__(self, files: dict[str, Path], threads: int | None = None):
        if threads:
            torch.set_num_threads(threads)
        started = time.perf_counter()
        self.detector = YOLO(str(files[DETECTOR]), task="detect")

        labels = json.loads(files[CLASSIFIER_LABELS].read_text())
        check_label_map(labels)
        self.classifier = torchvision.models.mobilenet_v3_large(num_classes=NUM_OUTPUTS)
        state = torch.load(files[CLASSIFIER], map_location="cpu", weights_only=True)
        self.classifier.load_state_dict(state)
        self.classifier.eval()

        self.colors = ColorParameters.from_json(json.loads(files[COLOR_PARAMETERS].read_text()))

        # One pass on a blank photo, so the first real scan isn't the slow one.
        blank = Image.new("RGB", (IMGSZ, IMGSZ), (200, 200, 200))
        self.detect(blank)
        self.classify(blank, [Found(100, 100, 300, 300, 1.0)])
        self.color(blank, [Found(100, 100, 300, 300, 1.0)])
        log.info(
            "Loaded the models (torch %s, %d threads) in %.1f s",
            torch.__version__,
            torch.get_num_threads(),
            time.perf_counter() - started,
        )

    def detect(self, image: Image.Image) -> list[Found]:
        result = self.detector.predict(
            image,
            imgsz=IMGSZ,
            conf=CONF,
            iou=IOU,
            max_det=MAX_DET,
            device="cpu",
            verbose=False,
        )[0]
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        return [
            Found(float(x1), float(y1), float(x2), float(y2), float(score))
            for (x1, y1, x2, y2), score in zip(boxes, scores, strict=True)
        ]

    def classify(self, image: Image.Image, found: list[Found]) -> list[Reading[PartId]]:
        batch = torch.stack(
            [TF.normalize(TF.to_tensor(letterbox(crop(image, f))), MEAN, STD) for f in found]
        )
        with torch.inference_mode():
            probabilities = torch.softmax(self.classifier(batch) / TEMPERATURE, dim=1)
        # Outputs 0–12 are the catalogue's parts; `other` only lowers their probability.
        confidence, index = probabilities[:, :OTHER_INDEX].max(dim=1)
        return [
            Reading(PART_IDS[int(i)], float(c))
            for i, c in zip(index.tolist(), confidence.tolist(), strict=True)
        ]

    def color(self, image: Image.Image, found: list[Found]) -> list[Reading[ColorId]]:
        lab = np.stack([warna_kepingan(crop(image, f), self.colors) for f in found])
        ids, confidence = putuskan(lab, self.colors)
        return [Reading(c, float(p)) for c, p in zip(ids, confidence, strict=True)]

"""The settings legoin-ml release v0.2.0's notes say the backend must use.

Kept apart from `models.py` so they can be checked without torch. A new model tag that
changes any of them is a code change here, with the release linked.
"""

from legoin_api.catalogue import DESIGN_IDS, PART_IDS

# Stage 1: the detector (best.pt, unchanged from v0.1.0).
IMGSZ = 640
CONF = 0.65
IOU = 0.7
MAX_DET = 300

# Ultralytics' save_one_box, which the crop check used: 1.02× plus 10 px.
CROP_GAIN = 1.02
CROP_PAD = 10

# Stage 2: the part classifier.
INPUT_SIZE = 224
PAD_COLOR = (124, 116, 104)
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
TEMPERATURE = 0.7493
NUM_OUTPUTS = 14
OTHER_INDEX = 13


def check_label_map(labels: dict) -> None:
    """Refuses a label map whose settings differ from the release notes this code follows."""
    expected = {
        "classes": list(PART_IDS),
        "design_ids": DESIGN_IDS,
        "num_outputs": NUM_OUTPUTS,
        "other_index": OTHER_INDEX,
        "input_size": INPUT_SIZE,
        "pad_color": list(PAD_COLOR),
        "mean": list(MEAN),
        "std": list(STD),
        "temperature": TEMPERATURE,
    }
    wrong = [key for key, value in expected.items() if labels.get(key) != value]
    if wrong:
        raise ValueError(f"The classifier's label map differs from the release notes: {wrong}")

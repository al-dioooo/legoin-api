"""Stage 3, the color: a port of notebook 03's `warna_kepingan`, `jarak` and `putuskan`.

Copied from section 5 of `notebooks/03-kmeans-cielab-brick-color.ipynb` in legoin-ml at
`bfee134`, as the v0.2.0 release notes ask, with its constants read from `legoin_warna.json`.
K-Means in CIELAB separates the brick from its background; the brick's color is the nearest
catalogue reference by CIEDE2000. The parameters are **provisional**: the references are
Rebrickable's colors, not yet calibrated on photos (`reference_source: "rebrickable"`).

Needs the `models` extra (NumPy, scikit-image, scikit-learn).
"""

import warnings
from dataclasses import dataclass

import numpy as np
from PIL import Image
from skimage.color import deltaE_ciede2000, rgb2lab
from sklearn.cluster import KMeans

from legoin_api.catalogue import COLOR_IDS, REBRICKABLE_COLOR_IDS


@dataclass(frozen=True)
class ColorParameters:
    colors: list[str]
    reference: np.ndarray  # (10, 3) L*, a*, b*
    temperature: float
    max_delta_e: float
    neutral_chroma: float
    kmeans_k: int
    kmeans_n_init: int
    kmeans_random_state: int
    analysis_size: int
    lightness_percentile: float
    border_fraction: float
    centre_sigma: float

    @classmethod
    def from_json(cls, data: dict) -> "ColorParameters":
        colors = list(data["colors"])
        if sorted(colors) != sorted(COLOR_IDS):
            raise ValueError(f"legoin_warna.json has colors {colors}, not the catalogue's")
        if data["rebrickable_ids"] != REBRICKABLE_COLOR_IDS:
            raise ValueError("legoin_warna.json's Rebrickable ids aren't the catalogue's")
        return cls(
            colors=colors,
            reference=np.array([data["reference_lab"][c] for c in colors], dtype=float),
            temperature=float(data["temperature"]),
            max_delta_e=float(data["max_delta_e"]),
            neutral_chroma=float(data["neutral_chroma"]),
            kmeans_k=int(data["kmeans_k"]),
            kmeans_n_init=int(data["kmeans_n_init"]),
            kmeans_random_state=int(data["kmeans_random_state"]),
            analysis_size=int(data["analysis_size"]),
            lightness_percentile=float(data["lightness_percentile"]),
            border_fraction=float(data["border_fraction"]),
            centre_sigma=float(data["centre_sigma"]),
        )


def warna_kepingan(im: Image.Image, p: ColorParameters) -> np.ndarray:
    """The brick's color (L*, a*, b*) from one crop."""
    im = im.convert("RGB")
    im.thumbnail((p.analysis_size, p.analysis_size))
    lab = rgb2lab(np.asarray(im) / 255.0)
    h, w = lab.shape[:2]
    px = lab.reshape(-1, 3)
    # A Gaussian weight on the centre and an 8% border ring: the box is centred on the brick,
    # and the background is at the edges.
    yy, xx = np.mgrid[:h, :w]
    s = p.centre_sigma
    centre = np.exp(
        -(((xx - (w - 1) / 2) / (s * w)) ** 2 + ((yy - (h - 1) / 2) / (s * h)) ** 2) / 2
    ).ravel()
    t = max(1, round(min(h, w) * p.border_fraction))
    border = np.ones((h, w), bool)
    border[t : h - t, t : w - t] = False
    border = border.ravel()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a crop with fewer than k distinct colors
        label = KMeans(
            p.kmeans_k, n_init=p.kmeans_n_init, random_state=p.kmeans_random_state
        ).fit_predict(px)
    # A cluster's score = its share of the centre weight - its share of the border ring;
    # a score above 0 means it's part of the brick.
    score = np.array(
        [
            centre[label == c].sum() / centre.sum() - (label[border] == c).mean()
            for c in range(p.kmeans_k)
        ]
    )
    brick = np.flatnonzero(score > 0) if (score > 0).any() else [int(score.argmax())]
    f = px[np.isin(label, brick)]
    return np.array(
        [np.percentile(f[:, 0], p.lightness_percentile), np.median(f[:, 1]), np.median(f[:, 2])]
    )


def jarak(lab: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """CIEDE2000 from each color (n, 3) to each reference (m, 3): (n, m)."""
    lab = np.atleast_2d(lab)
    return np.stack([deltaE_ciede2000(lab, np.broadcast_to(r, lab.shape)) for r in ref], 1)


def putuskan(lab: np.ndarray, p: ColorParameters) -> tuple[list[str], np.ndarray]:
    """Catalogue color ids and confidences (0 to 1) for brick colors (n, 3)."""
    lab = np.atleast_2d(lab)
    ref = p.reference
    d = jarak(lab, ref)
    z = -d / p.temperature
    prob = np.exp(z - z.max(1, keepdims=True))
    prob /= prob.sum(1, keepdims=True)
    i = prob.argmax(1)
    # A foreign color: too far from every reference, or clearly colored but named a neutral.
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    neutral = np.hypot(ref[i, 1], ref[i, 2]) < p.neutral_chroma
    foreign = (d.min(1) > p.max_delta_e) | ((chroma >= p.neutral_chroma) & neutral)
    confidence = np.where(foreign, np.minimum(prob.max(1), 0.5), prob.max(1))
    return [p.colors[k] for k in i], confidence

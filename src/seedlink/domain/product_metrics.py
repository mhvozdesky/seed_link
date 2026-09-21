"""Agreed product classifications shared by calculations and queries."""

from seedlink.domain.models import CropCategory
from seedlink.domain.normalization import normalize_name


def crop_category(value: str | None) -> CropCategory:
    normalized = normalize_name(value)
    if normalized == "sun: sunflowers":
        return CropCategory.SUNFLOWER
    if normalized == "crn: corn seeds":
        return CropCategory.CORN
    if normalized is None:
        return CropCategory.UNKNOWN
    return CropCategory.OTHER

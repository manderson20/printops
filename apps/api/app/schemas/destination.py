from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DestinationOut(BaseModel):
    id: UUID
    name: str
    short_name: str | None
    miles: float
    latitude: float | None
    longitude: float | None
    enabled: bool
    # Straight-line miles from the home location, when both ends have
    # coordinates. Shown next to `miles` on the settings page as the
    # floor the real driving distance must sit above — it is never
    # written to `miles`, because the sentence claims a drive.
    straight_line_miles: float | None = None

    model_config = {"from_attributes": True}


class DestinationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    short_name: str | None = Field(default=None, max_length=120)
    miles: float = Field(gt=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool = True

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class DestinationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    short_name: str | None = Field(default=None, max_length=120)
    miles: float | None = Field(default=None, gt=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool | None = None

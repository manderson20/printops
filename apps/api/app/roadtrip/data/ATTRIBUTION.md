# places.json

Derived from the GeoNames `cities1000` extract
(<https://download.geonames.org/export/dump/cities1000.zip>), downloaded
2026-09-05.

GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

## What was kept

Places in the US, Canada and Mexico with a population of 1,000 or more:
28,461 rows. Everywhere else was dropped because this list exists to name
places a district could plausibly *drive* to, and a routing service
cannot cross an ocean.

The floor is 1,000 rather than 15,000 because the list has two jobs and
they want different things. **Search** wants the small town fifteen
minutes down the road — Marceline, Missouri has 2,200 people and is the
first place this district would look for. **Suggestions** want somewhere
recognisable, and offering a village nobody outside the county has heard
of as a milestone would be worse than offering nothing; those are
filtered to 15,000 and up at the point of use
(`SUGGESTION_MIN_POPULATION` in `../places.py`).

Each row is a fixed-length array rather than an object, which is what
keeps the file at ~220 KB instead of ~700 KB:

    [name, admin1, country, latitude, longitude, population]

`admin1` is the two-letter state code, kept for US rows only. GeoNames
carries a bare number there for Canada and Mexico — "Toronto, 08" is not
a place anybody recognises — so those rows are named by country instead.

Coordinates are rounded to four decimal places — about 11 metres, which
is far finer than a milestone claiming "410 miles" needs.

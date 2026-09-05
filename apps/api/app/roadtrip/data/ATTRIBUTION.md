# places.json

Derived from the GeoNames `cities15000` extract
(<https://download.geonames.org/export/dump/cities15000.zip>), downloaded
2026-09-05.

GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

## What was kept

Cities in the US, Canada and Mexico with a population of 15,000 or more:
4,560 rows. Everywhere else was dropped because this list exists to
suggest places a district could plausibly *drive* to, and a routing
service cannot cross an ocean.

Each row is a fixed-length array rather than an object, which is what
keeps the file at ~220 KB instead of ~700 KB:

    [name, admin1, country, latitude, longitude, population]

`admin1` is the two-letter state code, kept for US rows only. GeoNames
carries a bare number there for Canada and Mexico — "Toronto, 08" is not
a place anybody recognises — so those rows are named by country instead.

Coordinates are rounded to four decimal places — about 11 metres, which
is far finer than a milestone claiming "410 miles" needs.

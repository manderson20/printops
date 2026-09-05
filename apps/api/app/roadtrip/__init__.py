"""Everything the road trip needs from outside itself: a road network to
ask for real driving routes, and a list of places worth driving to.

The split against app/reports/road_trip.py is by direction. That module
is pure — it turns rows the caller already has into a ladder and a route,
touching neither the network nor the clock, which is what makes every
branch of it directly testable. This package is the part that reaches
out: an HTTP call to a routing service, and a bundled dataset read off
disk. Nothing here runs while a dashboard is being rendered.
"""

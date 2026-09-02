#!/bin/bash
# Regenerate the 10-minute driving isochrone from Apple Park.
# Origin: Apple Park ring building center (OSM relation 5281838).
# Source: FOSSGIS public Valhalla server. Free-flow (speed-limit based), no traffic.
set -euo pipefail
cd "$(dirname "$0")/.."

REQ='{"locations":[{"lat":37.33484,"lon":-122.01139}],"costing":"auto","contours":[{"time":10}],"polygons":true,"denoise":0.3}'
ENCODED=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$REQ")

curl -sf --max-time 30 "https://valhalla1.openstreetmap.de/isochrone?json=${ENCODED}" -o data/isochrone_raw.json

python3 - <<'EOF'
import json, datetime
d = json.load(open('data/isochrone_raw.json'))
assert d.get('type') == 'FeatureCollection' and d.get('features'), 'unexpected isochrone response'
d['metadata'] = {
    'generated_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    'source': 'Valhalla (FOSSGIS public server, valhalla1.openstreetmap.de)',
    'costing': 'auto',
    'contour_minutes': 10,
    'denoise': 0.3,
    'origin': {'lat': 37.33484, 'lon': -122.01139, 'name': 'Apple Park (ring building center, OSM relation 5281838)'},
    'traffic': 'free-flow / speed-limit based; no live or historical traffic data'
}
json.dump(d, open('data/isochrone.json', 'w'), indent=1)
print('data/isochrone.json regenerated')
EOF

# The boundary of record is the isochrone itself (docs/DECISIONS.md D-2);
# rewrite boundary.json from it, then make sure the landmarks still fall inside.
python3 scripts/make_boundary.py
python3 scripts/verify.py

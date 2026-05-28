##philadelphia walkability calculator# #


##imports: sys for crashing, pandas is reading excelfiles, osmnx and networkx are for working with the street network (networkx is creation and manipulating complex networks whatever that means), pyproj is for coordinate transformations (web mercator to longitude and latitude) ##
import sys
import pandas as pd
import osmnx as ox
import networkx as nx
from pyproj import Transformer


##loading excel files##
EXCEL_FILE = "Vizcomp master dataset.xlsx"   
 
HOUSING  = "Affordable_Housing "
MEAL     = "Free_Meal_Sites"
GARDENS = "Registered_Community_Gardens"
MARKETS  = "Farmers_Markets"


##variable conversions and definitions##
max_miles  = 1.50
#according to google ai overview, most humans can walk 0.25 miles in about 5 minutes at an average pace##
meters_per_mile = 1609.34                       
max_walking_meters = max_miles * meters_per_mile


##converting coordinates from web mercator to longitude and latitude this was so hard to do im going to kill myself##
to_lonlat = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True) ##WAHT THE FUCKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK##
 
def convert(x, y):
    try:
        lon, lat = to_lonlat.transform(float(x), float(y))
        return lon, lat
    except Exception:
        return None, None

def load_sheet(sheet_name, xcol="X", ycol="Y"):
    df = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name)

    # Convert coordinates row by row
    lonlat = df.apply(lambda r: pd.Series(convert(r[xcol], r[ycol])), axis=1)
    lonlat.columns = ["lon", "lat"]
    df = pd.concat([df, lonlat], axis=1)
 
    # Edge case: drop rows where coordinate conversion failed (NaN lon/lat)
    bad = df["lon"].isna() | df["lat"].isna()
    if bad.sum() > 0:
        print(f"  Error: dropped {bad.sum()} rows from '{sheet_name}' with invalid coordinates")
    df = df[~bad].copy()
 
    # Edge case: sanity check that converted coords are actually in Philadelphia's area
    # Philadelphia is roughly lon -75.3 to -74.9, lat 39.85 to 40.15
    out_of_bounds = ~(
        df["lon"].between(-75.35, -74.85) &
        df["lat"].between(39.80, 40.20)
    )
    if out_of_bounds.sum() > 0:
        print(f"  Error: {out_of_bounds.sum()} rows in '{sheet_name}' have coordinates outside Philadelphia bounds")
 
    return df

##load data from excel sheets and convert coordinates##

print("Loading data from Excel workbook")
 
# Edge case: check the file exists before trying to load it
import os
if not os.path.exists(EXCEL_FILE):
    print(f"Error: could not find '{EXCEL_FILE}'")
    sys.exit(1) 

housing  = load_sheet(HOUSING)    
gardens  = load_sheet(GARDENS)   
markets  = load_sheet(MARKETS)   
 
## Meal sites special handling##
## coordinates are already long/lat, switched xys, and dropped the rows that have missing coordinates##

meals = pd.read_excel(EXCEL_FILE, sheet_name=MEAL)
meals = meals.dropna(subset=["x", "y"])
meals = meals.rename(columns={"x": "lat", "y": "lon"})   # fix the swap
meals = meals[
    meals["lon"].between(-180, 180) &    ##error handling for coordinates that are out of bounds##
    meals["lat"].between(-90, 90)        
].copy()
 
print(f"  Affordable housing projects : {len(housing)}")
print(f"  Meal sites (with coords)    : {len(meals)}")
print(f"  Community gardens           : {len(gardens)}")
print(f"  Farmers markets             : {len(markets)}") ##for some reason this would not work without f strings lol but whatever##


## now download street network will take a long time to load but hopefully will cache after first initial run oop##
print("\nDownloading Philadelphia pedestrian street network from OpenStreetMap")
 
G = ox.graph_from_place(
    "Philadelphia, Pennsylvania, USA",
    network_type="walk"     # pedestrian paths only, no highways or car-only roads
)
 
print(f"Network ready: {len(G.nodes):,} nodes, {len(G.edges):,} edges")

##okay this is the actual scoring part, we are going to calculate the shortest walking distance from each point of interest to the nearest node in the street network, and then assign points based on how far it is this is also subject to change based on everything else and everyone's thoughts##

def score(walk_meters):
    # Convert meters to miles, return points based on distance
    miles = walk_meters / meters_per_mile
    if miles <= 0.25: return 3.0   ##5 min walk##
    if miles <= 0.50: return 2.5   ##10 min walk##
    if miles <= 0.75: return 2.0   ##15 min walk##
    if miles <= 1.00: return 1.5   ##20 min walk##
    if miles <= 1.25: return 1.0   ##25 min walk##
    if miles <= 1.50: return 0.5   ##30 min walk##
    return 0.0                     ##>30 min walk##
 
 
def label(total): ##assigns a label based on the total score, this is also subject to change based on everything else and everyone's thoughts like honestly im just making this shit up as i go lol##
    if total >= 7.0: return "Excellent"
    if total >= 5.0: return "Good"
    if total >= 3.0: return "Fair"
    if total >= 0.1: return "Poor"  
    return "Very Poor"
 
print("\nSnapping all points to the street network") 

##i didn't think this was necessary but claude did so whatever it's just error handling i guess but basically this is using dots as stand ins for addresses that may not be at an intersection, the dot is now used in the routing, when every point is snapped in it's going to be easier to calculate and the difference is really just a few meters so pretty negligible##
 
def snap(lon, lat):
    # Returns the ID of the nearest street intersection to this point
    return ox.distance.nearest_nodes(G, lon, lat)
 
def snap_dataset(df, name_col):
    # Snaps every row and returns a list of (node_id, display_name) tuples
    return [
        (snap(row["lon"], row["lat"]), str(row[name_col]))
        for _, row in df.iterrows()
    ]
 
meal_nodes = snap_dataset(meals,   "site_name")
gd_nodes   = snap_dataset(gardens, "garden_name")
mk_nodes   = snap_dataset(markets, "name")
 
print("  Done.")

##now we're getting to the good part we now calculate the nearest amenity for each housing project and assign points based on the distance##

def nearest_amenity(housing_node, amenity_nodes):
    best_meters = float("inf") ##this is a placeholder for best distance SO FAR inf is inifinity so the first amenity we check will always be better, as the for loop goes on and on it'll check each amenity and if it's better it'll be replaced as the new best distance, if it's not better it'll just be ignored and the loop will keep going until it checks all amenities##
    best_name   = "" ##this is just setting up a variable to hold the name, it's an empty string rn but it'll be updated if we find a closer amenity##
 
    for amenity_node, name in amenity_nodes: ##holy MOLY IT'S A FOR LOOP!!!!!!!!!##
        try: ##a try except block to catch exceptions! ERROR HANDLING!!!!!!!##
            ##Dijkstra's algorithm: finds the shortest walking route in meters thank you claude lol##
            meters = nx.shortest_path_length(
                G, housing_node, amenity_node, weight="length" ##weight is length in meters, not intersections or turns## 
            )
        except nx.NetworkXNoPath:
            continue   ## error handling for if no path exists##
 
        if meters < best_meters:
            best_meters = meters
            best_name   = name
 
    # If nothing is within 0.25 miles, return zeros
    if best_meters > max_walking_meters:
        return 0, "", None
 
    return score(best_meters), best_name, round(best_meters / meters_per_mile, 3)

##now this scores the housing projects frfr##

print("\nScoring housing projects")
rows = []
 
for i, (_, h) in enumerate(housing.iterrows(), 1): ##holy moly a for loop!!!!!!!##
    name = h.get("project_name", f"Project {i}")
    print(f"  [{i}/{len(housing)}] {name}")
 
    ##Snap housing project to the nearest street intersection##
    h_node = snap(h["lon"], h["lat"])
 
    ##Get score, nearest amenity name, and walking distance for each category##
    score_ml, nearestname_ml, nearestdistance_ml = nearest_amenity(h_node, meal_nodes)
    score_gd, nearestname_gd, nearestdistance_gd = nearest_amenity(h_node, gd_nodes)
    score_mk, nearestname_mk, nearestdistance_mk = nearest_amenity(h_node, mk_nodes)
 
    total = score_ml + score_gd + score_mk
 
    rows.append({ ##this is now a dictionary. when the loop finishes rows is a list of all the housing projects with their scores and nearest amenities and distances##
        ##affordable housing project info##
        "project_name"         : h.get("project_name", ""),
        "address"              : h.get("address", ""),
        "status"               : h.get("status", ""),
        "total_units"          : h.get("total_units", ""),
        "lon"                  : round(h["lon"], 6),
        "lat"                  : round(h["lat"], 6),
 
        ##meal sites info##
        "meal_sites_score"     : score_ml,    
        "meal_sites_nearest"   : nearestname_ml,   
        "meal_sites_walk_mi"   : nearestdistance_ml,   
 
        ##community gardens info##
        "gardens_score"        : score_gd,
        "gardens_nearest"      : nearestname_gd,
        "gardens_walk_mi"      : nearestdistance_gd,
 
        ##farmers markets info##
        "markets_score"        : score_mk,
        "markets_nearest"      : nearestname_mk,
        "markets_walk_mi"      : nearestdistance_mk,
 
        ##totals info##
        "walkability_total"    : total,          # 0 to 9.0
        "walkability_label"    : label(total),   # Excellent / Good / Fair / Poor / Very Poor
    })

##thank god the last step, this is just saving the results to a new csv file and printing some summaries##

output = pd.DataFrame(rows).sort_values("walkability_total", ascending=False)
output.to_csv("affordable_housing_walkability.csv", index=False)
 
print(f"\nDone! Saved → affordable_housing_walkability.csv ({len(output)} rows)")
 
print("\n── Score label breakdown ──────────────────────────")
print(output["walkability_label"].value_counts().to_string())
 
print("\n── Average score per category ─────────────────────")
for col in ["meal_sites_score", "gardens_score", "markets_score"]:
    print(f"  {col:22s}: {output[col].mean():.2f} avg")
 
print("\n── Top 10 most walkable housing projects ──────────")
print(output[[
    "project_name", "walkability_total", "walkability_label",
    "meal_sites_score", "gardens_score", "markets_score"
]].head(10).to_string(index=False))

